"""V3 Part E: Advanced Signal Detector Service（§51-§56）。

固定 4 个 detector，全部 deterministic（无 LLM）：
- coordination_cluster：最新 succeeded integrity job 的 cluster_ids 为 scope
- actor_recurrence：Workspace identity component 跨 >=3 个 Investigation
- media_reuse：exact MediaAsset.actual_sha256 跨 >=2 个 Case（phash candidate
  不产生 Signal，§54）
- cross_case_overlap：active Cross Links 综合特征公式（§55）

每个 detector 先计算完整 expected set，再 upsert，最后
reconcile_detector_scope（§56）把本次 scope 内不再成立的 Signal 置
detector_active=false 并按 §11.2 生命周期处理（P0 requirement）。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from app.application.workspace_entity_service import WorkspaceEntityService
from app.core.v3 import ADVANCED_SIGNAL_VERSION
from app.infrastructure.database.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.database.cross_investigation_repository import (
    CrossInvestigationRepository,
)
from app.infrastructure.database.derived_signal_repository import (
    DerivedSignalRepository,
)
from app.infrastructure.database.integrity_repository import IntegrityRepository
from app.infrastructure.database.media_pipeline_repository import (
    MediaPipelineRepository,
)

# §52.1 coordination 阈值（固定，不得自行调整）
_COORDINATION_MIN_SIZE = 3
_COORDINATION_MIN_SCORE = 0.75
_COORDINATION_CRITICAL_SCORE = 0.90
_COORDINATION_CRITICAL_SIZE = 5

_ACTOR_RECURRENCE_MIN_CASES = 3
_ACTOR_RECURRENCE_CRITICAL_CASES = 5

_MEDIA_REUSE_MIN_CASES = 2
_MEDIA_REUSE_CRITICAL_CASES = 4

_OVERLAP_MIN_SCORE = 0.60
_OVERLAP_MIN_RELATION_TYPES = 2
_OVERLAP_CRITICAL_SCORE = 0.85


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()


def _evidence_type(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("type") or item.get("relation_type") or "other")
    return "other"


def _evidence_counts(evidence_refs: Sequence[Any]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in evidence_refs:
        counts[_evidence_type(item)] += 1
    return dict(counts)


class AdvancedSignalDetectorService:
    def __init__(
        self,
        *,
        derived_repository: DerivedSignalRepository,
        integrity_repository: IntegrityRepository,
        analysis_job_repository: AnalysisJobRepository,
        workspace_service: WorkspaceEntityService,
        cross_repository: CrossInvestigationRepository,
        media_repository: MediaPipelineRepository,
        application_repository: Any,
    ) -> None:
        self._derived = derived_repository
        self._integrity = integrity_repository
        self._jobs = analysis_job_repository
        self._workspace = workspace_service
        self._cross = cross_repository
        self._media = media_repository
        self._application = application_repository
        self._version = ADVANCED_SIGNAL_VERSION

    # ------------------------------------------------------------------
    # §52 coordination_cluster（per case，scope = 最新 succeeded integrity job）
    # ------------------------------------------------------------------

    async def _detect_coordination_for_case(
        self, case_id: str
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        """返回 (signals, expected_fingerprints)。

        无最新 succeeded integrity job → expected set = empty（§52），
        不修改旧 Monitor Signal。
        """
        job = await self._jobs.latest_succeeded(case_id, "integrity")
        if job is None:
            return [], [], [case_id]
        cluster_ids = list(job.result_json.get("cluster_ids") or [])
        signals: list[dict[str, Any]] = []
        expected: list[str] = []
        for cluster_id in cluster_ids:
            cluster = await self._integrity.get_cluster(cluster_id)
            if cluster is None:
                continue
            size = int(cluster.size or 0)
            score = float(cluster.score or 0.0)
            if size < _COORDINATION_MIN_SIZE or score < _COORDINATION_MIN_SCORE:
                continue
            severity = (
                "critical"
                if score >= _COORDINATION_CRITICAL_SCORE
                and size >= _COORDINATION_CRITICAL_SIZE
                else "warning"
            )
            member_ids = [
                str(member.get("account_id", ""))
                for member in (cluster.members or [])
                if isinstance(member, dict)
            ]
            signals.append(
                {
                    "fingerprint": _fingerprint(
                        "coordination_cluster", cluster_id, self._version
                    ),
                    "case_id": str(cluster.case_id or case_id),
                    "source_type": "derived",
                    "source_id": cluster_id,
                    "signal_type": "coordination_cluster",
                    "severity": severity,
                    "title": "检测到疑似协调行为模式",
                    "why_it_matters": (
                        f"Integrity 分析发现 {size} 个账号的协调行为模式"
                        f"（cluster score {score:.2f}），建议人工核查。"
                    ),
                    "confidence": None,
                    "metric_snapshot": {
                        "cluster_size": size,
                        "cluster_score": score,
                        "cluster_id": cluster_id,
                    },
                    "evidence_refs": [
                        {"account_id": account_id} for account_id in member_ids
                    ],
                    "related_case_ids": [str(cluster.case_id or case_id)],
                    "case_links": [str(cluster.case_id or case_id)],
                }
            )
            expected.append(signals[-1]["fingerprint"])
        return signals, expected, [case_id]

    # ------------------------------------------------------------------
    # §53 actor_recurrence（全局 component）
    # ------------------------------------------------------------------

    async def _detect_actor_recurrence(
        self,
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        components = await self._workspace.list_components_with_cases()
        scope_cases = sorted(
            {case_id for component in components for case_id in component["cases"]}
        )
        signals: list[dict[str, Any]] = []
        expected: list[str] = []
        for component in components:
            cases = component["cases"]
            if len(cases) < _ACTOR_RECURRENCE_MIN_CASES:
                continue
            severity = (
                "critical"
                if len(cases) >= _ACTOR_RECURRENCE_CRITICAL_CASES
                else "warning"
            )
            component_key = component["component_key"]
            signals.append(
                {
                    "fingerprint": _fingerprint(
                        "actor_recurrence", component_key, self._version
                    ),
                    "case_id": min(cases),
                    "source_type": "derived",
                    "source_id": component_key,
                    "signal_type": "actor_recurrence",
                    "severity": severity,
                    "title": "该主体在多个 Investigation 中重复出现",
                    "why_it_matters": (
                        f"同一身份主体出现在 {len(cases)} 个调查中"
                        f"（{', '.join(cases[:5])}），建议跨调查核查。"
                    ),
                    "confidence": None,
                    "metric_snapshot": {
                        "investigation_count": len(cases),
                        "entity_count": len(component["entity_ids"]),
                        "cases": cases,
                    },
                    "evidence_refs": [
                        {"entity_id": entity_id}
                        for entity_id in component["entity_ids"]
                    ],
                    "related_case_ids": cases,
                    "case_links": cases,
                }
            )
            expected.append(signals[-1]["fingerprint"])
        return signals, expected, scope_cases

    async def _detect_media_reuse(
        self,
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        rows = await self._media.list_sha_case_counts()
        scope_cases = sorted(
            {case_id for row in rows for case_id in (row.get("case_ids") or [])}
        )
        signals: list[dict[str, Any]] = []
        expected: list[str] = []
        for row in rows:
            sha256 = row["sha256"]
            case_count = int(row["case_count"])
            case_ids = list(row.get("case_ids") or [])
            if case_count < _MEDIA_REUSE_MIN_CASES:
                continue
            severity = (
                "critical"
                if case_count >= _MEDIA_REUSE_CRITICAL_CASES
                else "warning"
            )
            signals.append(
                {
                    "fingerprint": _fingerprint(
                        "media_reuse", sha256, self._version
                    ),
                    "case_id": min(case_ids) if case_ids else "",
                    "source_type": "derived",
                    "source_id": sha256,
                    "signal_type": "media_reuse",
                    "severity": severity,
                    "title": "同一媒体素材在多个调查中复用",
                    "why_it_matters": (
                        f"相同媒体素材（SHA256 {sha256[:12]}…）出现在 "
                        f"{case_count} 个调查中，建议核查来源一致性。"
                    ),
                    "confidence": None,
                    "metric_snapshot": {
                        "case_count": case_count,
                        "sha256": sha256,
                        "cases": case_ids,
                    },
                    "evidence_refs": [{"sha256": sha256}],
                    "related_case_ids": case_ids,
                    "case_links": case_ids,
                }
            )
            expected.append(signals[-1]["fingerprint"])
        return signals, expected, scope_cases

    # ------------------------------------------------------------------
    # §55 cross_case_overlap（全局 active links 特征公式）
    # ------------------------------------------------------------------

    async def _detect_cross_case_overlap(
        self,
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        links = await self._cross.list_workspace(limit=200)
        by_pair: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for link in links:
            pair = (str(link.left_case_id), str(link.right_case_id))
            by_pair[pair].append(link)
        scope_cases = sorted(
            {case_id for pair in by_pair for case_id in pair}
        )
        signals: list[dict[str, Any]] = []
        expected: list[str] = []
        for (left, right), pair_links in by_pair.items():
            counts: dict[str, int] = defaultdict(int)
            relation_types: set[str] = set()
            for link in pair_links:
                relation_types.add(link.relation_type)
                for etype, count in _evidence_counts(
                    link.evidence_refs_json or []
                ).items():
                    counts[etype] += count
            if len(relation_types) < _OVERLAP_MIN_RELATION_TYPES:
                continue
            actor = min(counts.get("actor", 0) / 3, 1.0)
            media = min(counts.get("media", 0) / 2, 1.0)
            content = min(counts.get("content", 0) / 5, 1.0)
            post = min(counts.get("post", 0) / 5, 1.0)
            score = actor * 0.40 + media * 0.30 + content * 0.20 + post * 0.10
            if score < _OVERLAP_MIN_SCORE:
                continue
            severity = "critical" if score >= _OVERLAP_CRITICAL_SCORE else "warning"
            signals.append(
                {
                    "fingerprint": _fingerprint(
                        "cross_case_overlap", left, right, self._version
                    ),
                    "case_id": left,
                    "source_type": "derived",
                    "source_id": f"{left}:{right}",
                    "signal_type": "cross_case_overlap",
                    "severity": severity,
                    "title": "多个独立关联特征显示两个调查存在较强重叠",
                    "why_it_matters": (
                        "多个独立关联特征显示两个 Investigation 之间存在较强"
                        f"重叠（score {score:.2f}），建议进一步核查。"
                    ),
                    "confidence": None,
                    "metric_snapshot": {
                        "overlap_score": score,
                        "relation_type_count": len(relation_types),
                        "relation_types": sorted(relation_types),
                        "evidence_counts": dict(counts),
                    },
                    "evidence_refs": [
                        {"relation_type": relation_type}
                        for relation_type in sorted(relation_types)
                    ],
                    "related_case_ids": [left, right],
                    "case_links": [left, right],
                }
            )
            expected.append(signals[-1]["fingerprint"])
        return signals, expected, scope_cases

    # ------------------------------------------------------------------
    # 编排：detector 逐个 flush + reconcile（§56）
    # ------------------------------------------------------------------

    async def _flush_detector(
        self,
        signal_type: str,
        signals: list[dict[str, Any]],
        expected: list[str],
        case_ids: list[str],
    ) -> dict[str, int]:
        upserted = 0
        for payload in signals:
            await self._derived.upsert_observed_signal(
                fingerprint=payload["fingerprint"],
                case_id=payload["case_id"],
                source_type=payload["source_type"],
                source_id=payload["source_id"],
                signal_type=payload["signal_type"],
                severity=payload["severity"],
                title=payload["title"],
                why_it_matters=payload["why_it_matters"],
                confidence=payload.get("confidence"),
                metric_snapshot=payload.get("metric_snapshot", {}),
                evidence_refs=payload.get("evidence_refs", []),
                related_case_ids=payload.get("related_case_ids", []),
                detector_version=self._version,
                case_links=payload.get("case_links") or payload.get(
                    "related_case_ids", []
                ),
            )
            upserted += 1
        stale = await self._derived.reconcile_detector_scope(
            signal_type=signal_type,
            detector_version=self._version,
            case_ids=case_ids,
            expected_fingerprints=expected,
        )
        return {"upserted": upserted, "stale_deactivated": stale}

    async def refresh_coordination(self, case_ids: Sequence[str]) -> dict[str, int]:
        """§52 coordination_cluster（per case，scope = 最新 succeeded integrity job）。"""
        signals: list[dict[str, Any]] = []
        expected: list[str] = []
        for case_id in case_ids:
            case_signals, case_expected, _ = await self._detect_coordination_for_case(
                case_id
            )
            signals.extend(case_signals)
            expected.extend(case_expected)
        return await self._flush_detector(
            "coordination_cluster", signals, expected, list(case_ids)
        )

    async def refresh_actor_recurrence(self) -> dict[str, int]:
        """§53 actor_recurrence（全局 identity component）。"""
        signals, expected, scope_cases = await self._detect_actor_recurrence()
        return await self._flush_detector(
            "actor_recurrence", signals, expected, scope_cases
        )

    async def refresh_media_reuse(self) -> dict[str, int]:
        """§54 media_reuse（全局 exact SHA）。"""
        signals, expected, scope_cases = await self._detect_media_reuse()
        return await self._flush_detector(
            "media_reuse", signals, expected, scope_cases
        )

    async def refresh_cross_case_overlap(self) -> dict[str, int]:
        """§55 cross_case_overlap（全局 active links 特征公式）。"""
        signals, expected, scope_cases = await self._detect_cross_case_overlap()
        return await self._flush_detector(
            "cross_case_overlap", signals, expected, scope_cases
        )

    async def refresh_all(self) -> dict[str, Any]:
        """全部 4 个 detector（全局 + 每 case coordination），逐 detector reconcile。"""
        cases = await self._application.list_cases()
        case_ids = [case.id for case in cases]
        return {
            "coordination_cluster": await self.refresh_coordination(case_ids),
            "actor_recurrence": await self.refresh_actor_recurrence(),
            "media_reuse": await self.refresh_media_reuse(),
            "cross_case_overlap": await self.refresh_cross_case_overlap(),
        }
