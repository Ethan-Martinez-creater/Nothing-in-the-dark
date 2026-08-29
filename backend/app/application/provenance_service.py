"""M4: Provenance service — relational links 聚合的一跳上下游。

支持 object_type：finding / artifact / evidence / claim / propagation_edge。
未知类型返回 provenance_object_type_unknown；跨 case 一律拒绝。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.errors import ApplicationError, ResourceNotFoundError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    ClaimRecord,
    EvidenceRecord,
    FindingEvidenceLinkRecord,
    FindingRecord,
    FindingSourceLinkRecord,
    PropagationEdgeRecord,
)

SUPPORTED_OBJECT_TYPES = {
    "finding",
    "artifact",
    "evidence",
    "claim",
    "propagation_edge",
}


class ProvenanceService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def one_hop(
        self, case_id: str, object_type: str, object_id: str
    ) -> dict[str, Any]:
        if object_type not in SUPPORTED_OBJECT_TYPES:
            raise ApplicationError(
                f"unknown provenance object type '{object_type}'",
                code="provenance_object_type_unknown",
            )
        resolver = getattr(self, f"_resolve_{object_type}")
        result = await resolver(case_id, object_id)
        if result is None:
            # 不区分"不存在"与"属于其他 case"，避免泄露他 case 对象存在性。
            # 404 语义复用 ResourceNotFoundError，code 遵循计划书错误码表。
            error = ResourceNotFoundError(f"case-scoped {object_type}", object_id)
            error.code = "provenance_object_not_found"
            raise error
        upstream, downstream = result
        return {
            "root": {"type": object_type, "id": object_id},
            "upstream": upstream,
            "downstream": downstream,
            "warnings": [],
        }

    # ---------------- resolvers（返回 (upstream, downstream) | None） ----------------

    async def _resolve_finding(
        self, case_id: str, finding_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        async with self._database.session_factory() as session:
            finding = await session.get(FindingRecord, finding_id)
            if finding is None or finding.case_id != case_id:
                return None
            links = (
                await session.execute(
                    select(FindingEvidenceLinkRecord).where(
                        FindingEvidenceLinkRecord.finding_id == finding_id
                    )
                )
            ).scalars().all()
            upstream = [
                {
                    "type": "evidence",
                    "id": link.evidence_ref,
                    "relation": link.relation,
                }
                for link in links
            ]
            sources = (
                await session.execute(
                    select(FindingSourceLinkRecord).where(
                        FindingSourceLinkRecord.finding_id == finding_id
                    )
                )
            ).scalars().all()
            upstream += [
                {
                    "type": link.source_type,
                    "id": link.source_id,
                    "relation": "derived_from",
                }
                for link in sources
            ]
            return upstream, []

    async def _resolve_artifact(
        self, case_id: str, artifact_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        async with self._database.session_factory() as session:
            # Artifact 归属由 case scope 校验（存在该 case 下的 finding source
            # link 即证明可引用）。
            links = (
                await session.execute(
                    select(FindingSourceLinkRecord, FindingRecord)
                    .join(
                        FindingRecord,
                        FindingRecord.id == FindingSourceLinkRecord.finding_id,
                    )
                    .where(
                        FindingSourceLinkRecord.source_type == "artifact",
                        FindingSourceLinkRecord.source_id == artifact_id,
                        FindingRecord.case_id == case_id,
                    )
                )
            ).all()
            if not links:
                return None
            downstream = [
                {
                    "type": "finding",
                    "id": finding.id,
                    "relation": "materialized_to",
                    "label": finding.title,
                }
                for _link, finding in links
            ]
            return [], downstream

    async def _resolve_evidence(
        self, case_id: str, evidence_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        async with self._database.session_factory() as session:
            evidence = await session.get(EvidenceRecord, evidence_id)
            if evidence is None or evidence.case_id != case_id:
                return None
            links = (
                await session.execute(
                    select(FindingEvidenceLinkRecord, FindingRecord)
                    .join(
                        FindingRecord,
                        FindingRecord.id == FindingEvidenceLinkRecord.finding_id,
                    )
                    .where(
                        FindingEvidenceLinkRecord.evidence_ref == evidence_id,
                        FindingRecord.case_id == case_id,
                    )
                )
            ).all()
            downstream = [
                {
                    "type": "finding",
                    "id": finding.id,
                    "relation": link.relation,
                    "label": finding.title,
                }
                for link, finding in links
            ]
            upstream: list[dict[str, Any]] = []
            if evidence.claim_id:
                upstream.append(
                    {"type": "claim", "id": str(evidence.claim_id), "relation": "attached_to"}
                )
            return upstream, downstream

    async def _resolve_claim(
        self, case_id: str, claim_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        async with self._database.session_factory() as session:
            claim = await session.get(ClaimRecord, claim_id)
            if claim is None or claim.case_id != case_id:
                return None
            evidence = (
                await session.execute(
                    select(EvidenceRecord).where(
                        EvidenceRecord.claim_id == claim_id,
                        EvidenceRecord.case_id == case_id,
                    )
                )
            ).scalars().all()
            downstream = [
                {"type": "evidence", "id": str(item.id), "relation": "supports"}
                for item in evidence
            ]
            return [], downstream

    async def _resolve_propagation_edge(
        self, case_id: str, edge_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        async with self._database.session_factory() as session:
            edge = await session.get(PropagationEdgeRecord, edge_id)
            if edge is None or edge.case_id != case_id:
                return None
            upstream: list[dict[str, Any]] = []
            for ref in edge.evidence_ids or []:
                upstream.append(
                    {"type": "evidence", "id": str(ref), "relation": "observed_in"}
                )
            links = (
                await session.execute(
                    select(FindingSourceLinkRecord, FindingRecord)
                    .join(
                        FindingRecord,
                        FindingRecord.id == FindingSourceLinkRecord.finding_id,
                    )
                    .where(
                        FindingSourceLinkRecord.source_type == "propagation_edge",
                        FindingSourceLinkRecord.source_id == edge_id,
                        FindingRecord.case_id == case_id,
                    )
                )
            ).all()
            downstream = [
                {
                    "type": "finding",
                    "id": finding.id,
                    "relation": "promoted_to",
                    "label": finding.title,
                }
                for _link, finding in links
            ]
            return upstream, downstream
