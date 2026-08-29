"""M4: Finding service — deterministic artifact materializer + status machine.

Agent 不直接产生 verified Finding：Expert Artifact 由本服务确定性物化为
``candidate``；``verified``/``rejected`` 只能由 Review 决策事务产生
（ApplicationRepository.decide_review_item 同事务同步，ReviewService 是
事实来源）；普通 Finding API 尝试设置终审态返回
``finding_review_required``。重复 sync 通过 source link 幂等键跳过，
绝不重置人工状态。
"""

from __future__ import annotations

from typing import Any

from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.finding_repository import FindingRepository
from app.infrastructure.database.models import ArtifactRecord, FindingRecord

SUPPORTED_ARTIFACT_KINDS = {"opinion_analysis", "fact_check"}

VALID_KINDS = {"opinion", "verification", "propagation", "narrative", "integrity", "manual"}

# 普通 Finding API 允许的状态迁移；verified/rejected 只能由 Review 决策
# 事务（ApplicationRepository.decide_review_item）产生，不在本表内。
ALLOWED_TRANSITIONS = {
    ("candidate", "under_review"),
    ("under_review", "candidate"),
    ("verified", "under_review"),
    ("rejected", "under_review"),
    ("candidate", "superseded"),
    ("under_review", "superseded"),
    ("verified", "superseded"),
    ("rejected", "superseded"),
}

# 目标状态为终审态时专用错误码：必须经 Review，不复用模糊的 invalid_transition。
REVIEW_ONLY_STATUSES = {"verified", "rejected"}

REVIEW_STATUS_TO_FINDING = {
    "unreviewed": "candidate",
    "in_review": "under_review",
    "needs_more_evidence": "under_review",
    "accepted": "verified",
    "rejected": "rejected",
    "superseded": "superseded",
}


def _clean_confidence(value: Any) -> float | None:
    if isinstance(value, (int, float)) and 0 <= float(value) <= 1:
        return float(value)
    return None


class FindingService:
    def __init__(self, database: Database, repository: ApplicationRepository) -> None:
        self._database = database
        self._repository = repository
        self._findings = FindingRepository(database)

    # ---------------- materializer ----------------

    async def sync_from_artifact(self, artifact: ArtifactRecord) -> dict[str, int]:
        """把单个 Expert Artifact 确定性物化为 candidate Findings（幂等）。"""
        if artifact.kind not in SUPPORTED_ARTIFACT_KINDS:
            return {"created": 0, "skipped": 0}
        data = artifact.data if isinstance(artifact.data, dict) else {}
        created = 0
        skipped = 0
        if artifact.kind == "opinion_analysis":
            conclusions = data.get("conclusions")
            if isinstance(conclusions, list):
                for index, conclusion in enumerate(conclusions):
                    if not isinstance(conclusion, dict):
                        continue
                    claim = str(conclusion.get("claim") or "").strip()
                    if not claim:
                        continue
                    source_path = f"conclusions[{index}]"
                    finding = await self._materialize(
                        artifact,
                        kind="opinion",
                        statement=claim,
                        confidence=_clean_confidence(conclusion.get("confidence")),
                        attributes={},
                        evidence_links=[
                            (str(evidence_id), "supports")
                            for evidence_id in (conclusion.get("evidence_ids") or [])
                            if str(evidence_id)
                        ],
                        source_path=source_path,
                    )
                    if finding is None:
                        skipped += 1
                    else:
                        created += 1
        elif artifact.kind == "fact_check":
            cards = data.get("cards")
            if isinstance(cards, list):
                for index, card in enumerate(cards):
                    if not isinstance(card, dict):
                        continue
                    claim = str(card.get("claim") or "").strip()
                    if not claim:
                        continue
                    source_path = f"cards[{index}]"
                    verdict = card.get("verdict")
                    links = [
                        (str(evidence_id), "supports")
                        for evidence_id in (card.get("supporting_evidence") or [])
                        if str(evidence_id)
                    ] + [
                        (str(evidence_id), "contradicts")
                        for evidence_id in (card.get("contradicting_evidence") or [])
                        if str(evidence_id)
                    ]
                    finding = await self._materialize(
                        artifact,
                        kind="verification",
                        statement=claim,
                        confidence=_clean_confidence(card.get("confidence")),
                        attributes={"verdict": verdict} if verdict else {},
                        evidence_links=links,
                        source_path=source_path,
                    )
                    if finding is None:
                        skipped += 1
                    else:
                        created += 1
        return {"created": created, "skipped": skipped}

    async def _materialize(
        self,
        artifact: ArtifactRecord,
        *,
        kind: str,
        statement: str,
        confidence: float | None,
        attributes: dict[str, Any],
        evidence_links: list[tuple[str, str]],
        source_path: str,
    ) -> FindingRecord | None:
        """创建 candidate Finding + source link；来源已存在时跳过（幂等）。"""
        existing = await self._findings.get_source_link(
            "artifact", str(artifact.id), source_path
        )
        if existing is not None:
            return None
        record = FindingRecord(
            case_id=artifact.case_id,
            kind=kind,
            title=statement[:80],
            statement=statement,
            status="candidate",
            confidence=confidence,
            attributes_json=attributes,
            source_run_id=artifact.run_id,
        )
        record = await self._findings.create(record)
        await self._findings.create_source_link(
            record.id, "artifact", str(artifact.id), source_path
        )
        for evidence_ref, relation in evidence_links:
            await self._findings.add_evidence_link(record.id, evidence_ref, relation)
        return record

    async def sync_case_history(self, case_id: str) -> dict[str, Any]:
        """历史同步：全量 artifacts 幂等物化；单个 malformed 不中断。"""
        artifacts = await self._repository.list_artifacts(case_id)
        created = 0
        skipped = 0
        unsupported = 0
        errors: list[dict[str, str]] = []
        for artifact in artifacts:
            if artifact.kind not in SUPPORTED_ARTIFACT_KINDS:
                unsupported += 1
                continue
            try:
                result = await self.sync_from_artifact(artifact)
                created += result["created"]
                skipped += result["skipped"]
            except Exception as exc:  # noqa: BLE001 - 单个失败不中断
                errors.append({"artifact_id": str(artifact.id), "error": str(exc)})
        return {
            "created": created,
            "skipped": skipped,
            "unsupported": unsupported,
            "errors": errors,
        }

    # ---------------- 手动 Finding ----------------

    async def create_manual(
        self,
        case_id: str,
        *,
        kind: str = "manual",
        title: str | None = None,
        statement: str,
        confidence: float | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        source_path: str = "",
        evidence_links: list[tuple[str, str]] | None = None,
    ) -> FindingRecord:
        if kind not in VALID_KINDS:
            raise ApplicationError(
                f"unknown finding kind '{kind}'", code="finding_invalid_transition"
            )
        if not statement.strip():
            raise ApplicationError(
                "finding statement must not be empty",
                code="finding_invalid_transition",
            )
        record = FindingRecord(
            case_id=case_id,
            kind=kind,
            title=(title or statement)[:200],
            statement=statement.strip(),
            status="candidate",
            confidence=_clean_confidence(confidence),
            attributes_json={},
        )
        record = await self._findings.create(record)
        if source_type and source_id:
            await self._findings.create_source_link(
                record.id, source_type, source_id, source_path
            )
        for evidence_ref, relation in evidence_links or []:
            await self._findings.add_evidence_link(record.id, evidence_ref, relation)
        return record

    # ---------------- 查询与状态机 ----------------

    async def get_for_case(self, case_id: str, finding_id: str) -> FindingRecord:
        record = await self._findings.get(finding_id)
        if record is None:
            raise ApplicationError(
                f"finding '{finding_id}' does not exist", code="finding_not_found"
            )
        if record.case_id != case_id:
            raise ApplicationError(
                "finding belongs to another case", code="finding_scope_mismatch"
            )
        return record

    async def list(
        self,
        case_id: str,
        *,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[FindingRecord]:
        return list(await self._findings.list(case_id, kind=kind, status=status, limit=limit))

    async def update_status(
        self, case_id: str, finding_id: str, status: str
    ) -> FindingRecord:
        if status in REVIEW_ONLY_STATUSES:
            raise ApplicationError(
                f"finding status '{status}' can only be set by a review decision",
                code="finding_review_required",
            )
        record = await self.get_for_case(case_id, finding_id)
        transition = (record.status, status)
        if transition not in ALLOWED_TRANSITIONS:
            raise ApplicationError(
                f"invalid finding transition {record.status} -> {status}",
                code="finding_invalid_transition",
            )
        updated = await self._findings.update_status(finding_id, status)
        assert updated is not None
        return updated

    # ---------------- evidence links ----------------

    async def add_evidence_link(
        self, case_id: str, finding_id: str, evidence_ref: str, relation: str
    ) -> FindingRecord:
        if relation not in {"supports", "contradicts", "context"}:
            raise ApplicationError(
                f"invalid evidence relation '{relation}'",
                code="finding_evidence_invalid",
            )
        await self.get_for_case(case_id, finding_id)
        await self._findings.add_evidence_link(finding_id, evidence_ref, relation)
        return (await self._findings.get(finding_id))  # type: ignore[return-value]

    async def remove_evidence_link(
        self, case_id: str, finding_id: str, evidence_ref: str, relation: str
    ) -> None:
        await self.get_for_case(case_id, finding_id)
        await self._findings.remove_evidence_link(finding_id, evidence_ref, relation)

    async def detail(
        self, case_id: str, finding_id: str
    ) -> dict[str, Any]:
        """聚合 detail（一次请求返回 links + sources，避免前端 5 连请求）。"""
        record = await self.get_for_case(case_id, finding_id)
        evidence_links = await self._findings.list_evidence_links(finding_id)
        sources = await self._findings.list_source_links(finding_id)
        return {
            "finding": record,
            "evidence_links": list(evidence_links),
            "sources": list(sources),
        }
