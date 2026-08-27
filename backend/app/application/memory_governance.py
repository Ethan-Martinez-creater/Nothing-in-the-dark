"""M23: memory governance application service.

把 services/memory_governance.py 的规则接到仓储与写入入口：

- 统一写入入口：组合 M16 内容安全风险分 + M23 类型/信任/证据 Gate +
  秘密扫描 + 冲突检测；deny 记录护栏决策与指标，不落库。
- 用户控制状态机：correct / disable / restore / delete / review。
- 重新索引（reindex）：范围/版本/dry-run，幂等，中断后恢复不产生重复向量。
- 维护：过期扫描 + 索引一致性收敛。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.core.errors import ApplicationError
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.schemas.knowledge import CreateMemoryRequest
from app.services.content_security import (
    DEFAULT_TRUST,
    TRUST_OPERATOR_INPUT,
    ContentEnvelope,
    ContentSecurityService,
)
from app.services.memory_governance import (
    MEMORY_STATUS_ACTIVE,
    MEMORY_STATUS_PENDING_REVIEW,
    MemoryWriteGate,
    WriteDecision,
    content_hash,
    detect_conflict,
    memory_type_for_kind,
    scan_for_secrets,
    sensitivity_of,
    status_transition,
)

logger = logging.getLogger(__name__)

_Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]

#: 越权 procedural 写入必须 0 成功率；高风险写入需审批（M21）由调用方接入。
HIGH_RISK_WRITE_REQUIRES_APPROVAL = True


class MemoryGovernanceService:
    """记忆安全与用户可控治理。"""

    def __init__(
        self,
        knowledge: KnowledgeRepository,
        security: ContentSecurityService | None = None,
        telemetry: Any = None,
        write_policy_version: str = "1.0",
    ) -> None:
        self._knowledge = knowledge
        self._security = security
        self._telemetry = telemetry
        self._write_policy_version = write_policy_version

    def _metric(self, name: str) -> None:
        if self._telemetry is not None:
            try:
                self._telemetry.metrics.increment(name)
            except Exception:  # noqa: BLE001
                pass

    # ---- 写入评估与持久化 ----

    async def evaluate_write(
        self,
        *,
        content: str,
        memory_type: str,
        source_type: str,
        source_id: str,
        trust_level: str | None = None,
        confidence: float = 1,
        has_evidence: bool = False,
        explicit_user_input: bool = False,
        run_id: str | None = None,
    ) -> WriteDecision:
        """组合评估：M16 风险分 + 秘密扫描 + M23 类型 Gate。"""
        risk_score = 0.0
        if self._security is not None:
            envelope = ContentEnvelope(
                content=content,
                source_type=source_type,
                source_id=source_id,
                trust=trust_level or DEFAULT_TRUST,
            )
            assessment = self._security.assess(
                envelope, object_type="memory_write", object_id=source_id
            )
            risk_score = assessment.score
        has_secret, _ = scan_for_secrets(content)
        if has_secret:
            self._metric("memory.writes_blocked")
            return WriteDecision(
                "deny", "内容包含秘密/凭据，拒绝写入并记录安全事件"
            )
        decision = MemoryWriteGate.evaluate(
            memory_type=memory_type,
            trust_level=trust_level or DEFAULT_TRUST,
            risk_score=risk_score,
            has_evidence=has_evidence,
            conflicting=False,
            explicit_user_input=explicit_user_input,
        )
        if decision.decision == "deny":
            self._metric("memory.writes_blocked")
        return decision

    async def persist_governed(
        self,
        *,
        case_id: str | None,
        request: CreateMemoryRequest,
        memory_type: str | None = None,
        trust_level: str | None = None,
        decision: WriteDecision | None = None,
        has_evidence: bool = False,
        explicit_user_input: bool = False,
        embedding: list[float] | None = None,
        run_id: str | None = None,
        expires_at: datetime | None = None,
        actor: str = "system",
    ) -> Any:
        """按 Gate 决策持久化：deny 抛 403；needs_review 落 pending_review。

        矛盾内容：与既有事实冲突时创建冲突记录而不是静默覆盖。
        """
        resolved_type = memory_type or memory_type_for_kind(request.kind)
        if decision is None:
            decision = await self.evaluate_write(
                content=request.content,
                memory_type=resolved_type,
                source_type=request.source_type,
                source_id=request.source_id,
                trust_level=trust_level,
                confidence=request.confidence,
                has_evidence=has_evidence,
                explicit_user_input=explicit_user_input,
                run_id=run_id,
            )
        if decision.decision == "deny":
            raise ApplicationError(
                decision.reason, code="memory_write_denied"
            )
        # 冲突检测：事实/假设类与既有活跃事实矛盾 -> 生成冲突记录。
        existing = await self._knowledge.list_memories(
            case_id,
            include_inactive=True,
            scope=request.scope,
        )
        conflicts = detect_conflict(
            request.content,
            [
                {
                    "id": m.id,
                    "content": m.content,
                    "memory_type": m.memory_type,
                    "status": m.status,
                }
                for m in existing
            ],
            memory_type=resolved_type,
        )
        status = (
            MEMORY_STATUS_PENDING_REVIEW
            if decision.needs_review or conflicts
            else MEMORY_STATUS_ACTIVE
        )
        record = await self._knowledge.create_memory(
            case_id,
            request,
            embedding=embedding,
            memory_type=resolved_type,
            trust_level=trust_level,
            review_state=decision.review_state,
            status=status,
            sensitivity=sensitivity_of(request.content),
            expires_at=expires_at,
            write_policy_version=self._write_policy_version,
        )
        for conflict in conflicts:
            await self._knowledge.add_conflict(
                record.id,
                str(conflict["memory_id"]),
                content_hash=content_hash(request.content),
            )
            self._metric("memory.conflicts")
        return record

    # ---- 用户控制状态机 ----

    async def disable_memory(
        self, memory_id: str, *, actor: str = "operator", reason: str = ""
    ) -> Any:
        record = await self._knowledge.get_memory(memory_id)
        if record is None:
            raise ApplicationError("memory not found", code="resource_not_found")
        target = status_transition(record.status, "disable")
        if target is None:
            raise ApplicationError(
                f"cannot disable memory in status {record.status}",
                code="memory_status_conflict",
            )
        self._metric("memory.mutations")
        return await self._knowledge.set_memory_status(
            memory_id,
            new_status=target,
            action="disable",
            actor=actor,
            reason=reason,
        )

    async def restore_memory(
        self, memory_id: str, *, actor: str = "operator", reason: str = ""
    ) -> Any:
        record = await self._knowledge.get_memory(memory_id)
        if record is None:
            raise ApplicationError("memory not found", code="resource_not_found")
        target = status_transition(record.status, "restore")
        if target is None:
            raise ApplicationError(
                f"cannot restore memory in status {record.status}",
                code="memory_status_conflict",
            )
        self._metric("memory.mutations")
        return await self._knowledge.set_memory_status(
            memory_id,
            new_status=target,
            action="restore",
            actor=actor,
            reason=reason,
        )

    async def delete_memory(
        self, memory_id: str, *, actor: str = "operator", reason: str = ""
    ) -> Any:
        record = await self._knowledge.get_memory(memory_id)
        if record is None:
            raise ApplicationError("memory not found", code="resource_not_found")
        if record.status == "deleted":
            return record
        target = status_transition(record.status, "delete")
        if target is None:
            raise ApplicationError(
                f"cannot delete memory in status {record.status}",
                code="memory_status_conflict",
            )
        self._metric("memory.mutations")
        return await self._knowledge.set_memory_status(
            memory_id,
            new_status=target,
            action="delete",
            actor=actor,
            reason=reason,
        )

    async def review_memory(
        self,
        memory_id: str,
        *,
        accept: bool,
        actor: str = "operator",
        reason: str = "",
    ) -> Any:
        """审核：accepted -> active；rejected -> disabled（可撤销）。"""
        record = await self._knowledge.get_memory(memory_id)
        if record is None:
            raise ApplicationError("memory not found", code="resource_not_found")
        action = "review_accept" if accept else "review_reject"
        target = status_transition(record.status, action)
        if target is None:
            raise ApplicationError(
                f"cannot review memory in status {record.status}",
                code="memory_status_conflict",
            )
        updated = await self._knowledge.set_memory_status(
            memory_id,
            new_status=target,
            action="review",
            actor=actor,
            reason=reason,
        )
        if updated is not None:
            updated = await self._knowledge.set_memory_review_state(
                memory_id,
                review_state="accepted" if accept else "rejected",
                last_verified_at=datetime.now(UTC),
            )
        self._metric("memory.mutations")
        return updated

    async def correct_memory(
        self,
        memory_id: str,
        request: CreateMemoryRequest,
        *,
        actor: str = "operator",
        reason: str = "",
        embedding: list[float] | None = None,
    ) -> Any:
        """修正产生新版本：旧版本 superseded，不再检索；历史可审计。"""
        record = await self._knowledge.get_memory(memory_id)
        if record is None:
            raise ApplicationError("memory not found", code="resource_not_found")
        decision = await self.evaluate_write(
            content=request.content,
            memory_type=record.memory_type or memory_type_for_kind(record.kind),
            source_type=record.source_type,
            source_id=record.source_id,
            trust_level=record.trust_level,
            confidence=request.confidence,
            has_evidence=True,
            explicit_user_input=record.trust_level == TRUST_OPERATOR_INPUT,
            run_id=None,
        )
        if decision.decision == "deny":
            raise ApplicationError(decision.reason, code="memory_write_denied")
        corrected_request = request.model_copy(
            update={"scope": record.scope, "kind": record.kind}
        )
        self._metric("memory.mutations")
        return await self._knowledge.apply_memory_correction(
            memory_id,
            corrected_request,
            actor=actor,
            reason=reason,
            embedding=embedding,
            memory_type=record.memory_type,
            trust_level=record.trust_level,
            review_state=decision.review_state,
            status=(
                MEMORY_STATUS_PENDING_REVIEW
                if decision.needs_review
                else MEMORY_STATUS_ACTIVE
            ),
            sensitivity=sensitivity_of(request.content),
            write_policy_version=self._write_policy_version,
        )

    # ---- 检索与访问记录 ----

    async def retrievable_memories(
        self,
        case_id: str | None = None,
        *,
        scope: str | None = None,
        memory_type: str | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        """普通上下文检索：仅 active（pending_review/expired/disabled/deleted 排除）。"""
        records = await self._knowledge.list_memories(
            case_id,
            scope=scope,
            memory_type=memory_type,
            status=MEMORY_STATUS_ACTIVE,
            limit=limit,
        )
        return list(records)

    async def record_access(
        self,
        memory_ids: list[str],
        *,
        run_id: str | None = None,
        purpose: str = "context",
    ) -> None:
        for memory_id in memory_ids:
            try:
                await self._knowledge.add_access_event(
                    memory_id, run_id=run_id, purpose=purpose
                )
            except Exception:  # noqa: BLE001 - 访问审计失败不阻断上下文
                logger.exception("access event failed for memory %s", memory_id)

    # ---- 重新索引（幂等、可 dry-run、中断后可恢复） ----

    async def reindex(
        self,
        *,
        scope: str | None = None,
        status: str | None = None,
        memory_type: str | None = None,
        dry_run: bool = False,
        embedding_version: str = "1.0",
        embedder: _Embedder | None = None,
        limit: int = 500,
    ) -> dict[str, object]:
        """重新索引：仅处理缺向量的可检索记忆；dry_run 返回计划。

        幂等：已 indexed 的记忆跳过；中断后再次运行只处理剩余 pending，
        不产生重复 chunk/embedding（embedding 直接覆盖写回单行）。
        """
        candidates = [
            m
            for m in await self._knowledge.list_index_stale_memories(limit=limit)
            if (scope is None or m.scope == scope)
            and (status is None or m.status == status)
            and (memory_type is None or m.memory_type == memory_type)
        ]
        if dry_run or embedder is None:
            return {
                "dry_run": dry_run,
                "planned": len(candidates),
                "scanned_limit": limit,
                "embedding_version": embedding_version,
            }
        if not candidates:
            return {
                "dry_run": False,
                "processed": 0,
                "embedding_version": embedding_version,
            }
        contents = [m.content for m in candidates]
        vectors = await embedder(contents)
        processed = 0
        for memory, vector in zip(candidates, vectors, strict=False):
            if vector is None:
                continue
            await self._knowledge.mark_memory_indexed(
                memory.id,
                embedding=vector,
                embedding_version=embedding_version,
            )
            processed += 1
        return {
            "dry_run": False,
            "processed": processed,
            "skipped": len(candidates) - processed,
            "embedding_version": embedding_version,
        }

    # ---- 维护：过期扫描 + 索引一致性 ----

    async def maintenance(self, *, now: datetime | None = None) -> dict[str, object]:
        now = now or datetime.now(UTC)
        expired = await self._knowledge.scan_expired_memories(now)
        stale = await self._knowledge.list_index_stale_memories()
        return {
            "expired": len(expired),
            "index_stale": len(stale),
            "checked_at": now.isoformat(),
        }
