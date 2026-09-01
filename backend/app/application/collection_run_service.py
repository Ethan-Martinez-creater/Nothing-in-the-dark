"""Async progressive collection run orchestration (M-async-progressive).

CollectionRunService 负责：解析审批冻结的 exact snapshot（INV-1）、
构建 Discovery/Deep budget、生成 request fingerprint、幂等创建
CollectionRun、查询与取消。不负责实际 Crawl（由 CollectionRunWorker
执行）。

- Discovery：默认不抓评论（include_comments=false, comment_limit=0），
  每天每平台保留 30 条；平台 aggregate 上游预算
  ``min(max(days*10, 60), 150)``。
- Deep：显式用户动作，重新 Approval；抓评论（comment_limit<=10），
  aggregate 预算可更高但必须进入 Approval Scope。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from app.application.collection_service import CollectionDefinitionService
from app.core.errors import ApplicationError
from app.infrastructure.database.collection_run_repository import CollectionRunRepository
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import CaseRecord, CollectionRunRecord

PHASES = ("discovery", "deep")

DISCOVERY_PER_DAY_LIMIT = 30
DEEP_PER_DAY_LIMIT = 150
DEEP_COMMENT_LIMIT = 10


def inclusive_days(time_range: dict[str, str | None]) -> int:
    try:
        start = datetime.fromisoformat(
            str((time_range or {}).get("start") or "").replace("Z", "+00:00")
        )
        end = datetime.fromisoformat(
            str((time_range or {}).get("end") or "").replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return 1
    return max((end.date() - start.date()).days + 1, 1)


def discovery_budget(time_range: dict[str, str | None]) -> dict[str, Any]:
    days = inclusive_days(time_range)
    upstream = min(max(days * 10, 60), 150)
    return {
        "limit_per_platform": upstream,
        "per_day_limit": DISCOVERY_PER_DAY_LIMIT,
        "upstream_limit_per_platform": upstream,
        "include_comments": False,
        "comment_limit": 0,
    }


def deep_budget(time_range: dict[str, str | None]) -> dict[str, Any]:
    days = inclusive_days(time_range)
    upstream = min(max(days * 30, 150), 600)
    return {
        "limit_per_platform": upstream,
        "per_day_limit": DEEP_PER_DAY_LIMIT,
        "upstream_limit_per_platform": upstream,
        "include_comments": True,
        "comment_limit": DEEP_COMMENT_LIMIT,
    }


def budget_for_phase(
    phase: str, time_range: dict[str, str | None]
) -> dict[str, Any]:
    if phase == "discovery":
        return discovery_budget(time_range)
    if phase == "deep":
        return deep_budget(time_range)
    raise ApplicationError(
        f"unknown collection phase '{phase}'", code="collection_phase_invalid"
    )


class CollectionRunService:
    def __init__(
        self,
        database: Database,
        collection_service: CollectionDefinitionService,
        repository: CollectionRunRepository | None = None,
    ) -> None:
        self._database = database
        self._collection_service = collection_service
        self._repository = repository or CollectionRunRepository(database)

    # ---------------- exact snapshot ----------------

    async def _load_case(self, case_id: str) -> CaseRecord:
        async with self._database.session_factory() as session:
            case = await session.get(CaseRecord, case_id)
        if case is None:
            raise ApplicationError(
                f"case '{case_id}' does not exist", code="collection_scope_mismatch"
            )
        return case

    def _resolve_platforms(
        self, case: CaseRecord, requested: list[str] | None
    ) -> list[str]:
        case_platforms = list(case.platforms or [])
        if not case_platforms:
            raise ApplicationError(
                "case has no platforms to collect",
                code="collection_validation_failed",
            )
        if not requested:
            return list(case_platforms)
        allowed = [p for p in requested if p in case_platforms]
        if not allowed:
            raise ApplicationError(
                "requested platforms are not a subset of the case platforms",
                code="collection_validation_failed",
            )
        return allowed

    async def build_snapshot(
        self,
        case_id: str,
        *,
        phase: str,
        platforms: list[str] | None = None,
        time_range: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        """构建审批冻结的 immutable snapshot（INV-1）。"""
        if phase not in PHASES:
            raise ApplicationError(
                f"unknown collection phase '{phase}'", code="collection_phase_invalid"
            )
        case = await self._load_case(case_id)
        active = await self._collection_service.get_active(case_id)
        if active is None:
            raise ApplicationError(
                "case has no active collection definition; activate one first",
                code="collection_not_found",
            )
        resolved_platforms = self._resolve_platforms(case, platforms)
        effective_time_range = dict(
            time_range if time_range is not None else (case.time_range or {})
        )
        keywords = self._collection_service.keywords_for(
            active,
            requested_platforms=resolved_platforms,
            fallback_topic=case.topic,
        )
        budget = budget_for_phase(phase, effective_time_range)
        return {
            "case_id": case_id,
            "definition": {"id": active.id, "version": active.version},
            "phase": phase,
            "topic": case.topic,
            "platforms": resolved_platforms,
            "time_range": effective_time_range,
            "keywords": {
                platform: list(groups) for platform, groups in keywords.items()
            },
            "exclusions": list(active.exclusions or []),
            "filters": dict(active.filters or {}),
            "budget": budget,
        }

    async def resolve_approval_scope(
        self,
        case_id: str,
        *,
        phase: str,
        platforms: list[str] | None = None,
        time_range: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        """把 exact snapshot 投影为 Approval Scope（AC4）。"""
        snapshot = await self.build_snapshot(
            case_id, phase=phase, platforms=platforms, time_range=time_range
        )
        budget = snapshot["budget"]
        return {
            "kind": "collection_run",
            "phase": phase,
            "collection_definition_id": snapshot["definition"]["id"],
            "collection_definition_version": snapshot["definition"]["version"],
            "platforms": sorted(snapshot["platforms"]),
            "start": str(snapshot["time_range"].get("start") or ""),
            "end": str(snapshot["time_range"].get("end") or ""),
            "limit_per_platform": int(budget["limit_per_platform"]),
            "per_day_limit": int(budget["per_day_limit"]),
            "upstream_limit_per_platform": int(budget["upstream_limit_per_platform"]),
            "include_comments": bool(budget["include_comments"]),
            "comment_limit": int(budget["comment_limit"]),
        }

    @staticmethod
    def build_fingerprint(snapshot: dict[str, Any]) -> str:
        """canonical request payload 的 SHA256（文档 23 节）。"""
        canonical = {
            "case_id": snapshot["case_id"],
            "definition_id": snapshot["definition"]["id"],
            "definition_version": snapshot["definition"]["version"],
            "phase": snapshot["phase"],
            "platforms": sorted(snapshot["platforms"]),
            "time_range": {
                "start": str(snapshot["time_range"].get("start") or ""),
                "end": str(snapshot["time_range"].get("end") or ""),
            },
            "keywords": {
                platform: sorted(set(groups))
                for platform, groups in sorted(
                    (snapshot["keywords"] or {}).items()
                )
            },
            "exclusions": sorted(snapshot["exclusions"] or []),
            "filters": snapshot["filters"] or {},
            "budget": dict(sorted((snapshot["budget"] or {}).items())),
        }
        raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ---------------- idempotent start / read / cancel ----------------

    async def start(
        self,
        case_id: str,
        *,
        phase: str,
        trigger_run_id: str | None = None,
        trigger_turn_id: str | None = None,
        trigger_tool_call_id: str | None = None,
        approval_id: str | None = None,
        platforms: list[str] | None = None,
        time_range: dict[str, str | None] | None = None,
        idempotency_key: str | None = None,
    ) -> CollectionRunRecord:
        """创建 queued run（Active Equivalent 存在时返回已有 run）。"""
        snapshot = await self.build_snapshot(
            case_id, phase=phase, platforms=platforms, time_range=time_range
        )
        fingerprint = self.build_fingerprint(snapshot)
        existing = await self._repository.find_active_by_fingerprint(
            case_id, fingerprint
        )
        if existing is not None:
            return existing
        return await self._repository.create(
            case_id=case_id,
            phase=phase,
            request_fingerprint=fingerprint,
            request_json=snapshot,
            collection_definition_id=snapshot["definition"]["id"],
            collection_definition_version=snapshot["definition"]["version"],
            trigger_run_id=trigger_run_id,
            trigger_turn_id=trigger_turn_id,
            trigger_tool_call_id=trigger_tool_call_id,
            approval_id=approval_id,
            idempotency_key=idempotency_key,
        )

    async def get_for_case(self, case_id: str, run_id: str) -> CollectionRunRecord:
        return await self._repository.get_for_case(case_id, run_id)

    async def list_for_case(
        self,
        case_id: str,
        *,
        active_only: bool = False,
        status: str | None = None,
        phase: str | None = None,
        limit: int = 20,
    ) -> list[CollectionRunRecord]:
        return list(
            await self._repository.list_for_case(
                case_id,
                active_only=active_only,
                status=status,
                phase=phase,
                limit=limit,
            )
        )

    async def list_active_for_case(
        self, case_id: str, *, limit: int = 10
    ) -> list[CollectionRunRecord]:
        return list(await self._repository.list_active_for_case(case_id, limit=limit))

    async def cancel(self, case_id: str, run_id: str) -> CollectionRunRecord:
        return await self._repository.request_cancel(run_id)
