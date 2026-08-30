"""M4/C5: Provenance service — relational links 聚合的一跳上下游。

支持 object_type：finding / artifact / evidence / claim / propagation_edge /
report_document。未知类型返回 provenance_object_type_unknown；跨 case 一律
拒绝（统一 provenance_object_not_found，不泄露他 case 对象存在性）。

C5 变更：
- artifact resolver 直接读 ArtifactRecord（无 Finding 的真实 Artifact 仍可查）；
- finding 的 evidence upstream 逐条校验存在性，历史脏 link 以
  ``dangling_evidence_ref`` warning 输出而非伪造 Evidence node；
- finding downstream 接 ReportDocument（复用 C3 citation normalizer）；
- 新增 report_document root（upstream=citations，downstream=后续 revision）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.application.report_document_service import normalize_citation_refs
from app.core.errors import ApplicationError, ResourceNotFoundError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    ArtifactRecord,
    ClaimRecord,
    EvidenceRecord,
    FindingEvidenceLinkRecord,
    FindingRecord,
    FindingSourceLinkRecord,
    PropagationEdgeRecord,
    ReportDocumentRecord,
)

SUPPORTED_OBJECT_TYPES = {
    "finding",
    "artifact",
    "evidence",
    "claim",
    "propagation_edge",
    "report_document",
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
        upstream, downstream, warnings = result
        return {
            "root": {"type": object_type, "id": object_id},
            "upstream": upstream,
            "downstream": downstream,
            "warnings": warnings,
        }

    # ---------------- resolvers（返回 (upstream, downstream, warnings) | None） ----------------

    async def _resolve_finding(
        self, case_id: str, finding_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]] | None:
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
            upstream: list[dict[str, Any]] = []
            warnings: list[dict[str, str]] = []
            for link in links:
                # C5：坏 link（不存在/跨 case）不输出伪造 Evidence node
                evidence = await session.get(EvidenceRecord, link.evidence_ref)
                if evidence is None or evidence.case_id != case_id:
                    warnings.append(
                        {
                            "type": "dangling_evidence_ref",
                            "evidence_ref": link.evidence_ref,
                            "relation": link.relation,
                        }
                    )
                    continue
                upstream.append(
                    {
                        "type": "evidence",
                        "id": link.evidence_ref,
                        "relation": link.relation,
                    }
                )
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
            downstream = await self._reports_citing_finding(session, case_id, finding_id)
            return upstream, downstream, warnings

    async def _resolve_artifact(
        self, case_id: str, artifact_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]] | None:
        async with self._database.session_factory() as session:
            # C5：直接读取 ArtifactRecord 并校验 case scope —— 不再依赖
            # FindingSourceLink 间接判断存在性（未物化 Finding 的真实
            # Artifact 也可查询 provenance）。
            artifact = await session.get(ArtifactRecord, artifact_id)
            if artifact is None or artifact.case_id != case_id:
                return None
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
            downstream = [
                {
                    "type": "finding",
                    "id": finding.id,
                    "relation": "materialized_to",
                    "label": finding.title,
                }
                for _link, finding in links
            ]
            return [], downstream, []

    async def _resolve_evidence(
        self, case_id: str, evidence_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]] | None:
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
            return upstream, downstream, []

    async def _resolve_claim(
        self, case_id: str, claim_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]] | None:
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
            return [], downstream, []

    async def _resolve_propagation_edge(
        self, case_id: str, edge_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]] | None:
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
            return upstream, downstream, []

    async def _resolve_report_document(
        self, case_id: str, report_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]] | None:
        async with self._database.session_factory() as session:
            report = await session.get(ReportDocumentRecord, report_id)
            if report is None or report.case_id != case_id:
                return None
            upstream: list[dict[str, Any]] = []
            warnings: list[dict[str, str]] = []
            content = report.content_json if isinstance(report.content_json, dict) else {}
            citation_links = content.get("citation_links") or []
            for index, link in enumerate(citation_links):
                refs = normalize_citation_refs(link, index)
                if refs is None:
                    warnings.append(
                        {"type": "dangling_citation_ref", "field": f"citation_links[{index}]"}
                    )
                    continue
                for ref_type, ref_id, _path in refs:
                    resolved_type = ref_type
                    if ref_type == "generic":
                        resolved_type = await self._resolve_generic_ref_type(
                            session, case_id, ref_id
                        )
                    if resolved_type is None:
                        warnings.append(
                            {
                                "type": "dangling_citation_ref",
                                "field": f"citation_links[{index}]",
                                "ref": ref_id,
                            }
                        )
                        continue
                    upstream.append(
                        {"type": resolved_type, "id": ref_id, "relation": "cited"}
                    )
            # downstream：同 family 的后续 revision（supersedes 链向后）
            revisions = (
                await session.execute(
                    select(ReportDocumentRecord).where(
                        ReportDocumentRecord.supersedes_id == report_id,
                        ReportDocumentRecord.case_id == case_id,
                    )
                )
            ).scalars().all()
            downstream = [
                {
                    "type": "report_document",
                    "id": str(revision.id),
                    "relation": "superseded_by",
                    "label": revision.title,
                }
                for revision in revisions
            ]
            return upstream, downstream, warnings

    # ---------------- helpers ----------------

    async def _reports_citing_finding(
        self, session: Any, case_id: str, finding_id: str
    ) -> list[dict[str, Any]]:
        """Finding downstream：当前 case 内 citations 引用该 finding 的报告。"""
        reports = (
            await session.execute(
                select(ReportDocumentRecord).where(ReportDocumentRecord.case_id == case_id)
            )
        ).scalars().all()
        downstream: list[dict[str, Any]] = []
        for report in reports:
            content = report.content_json if isinstance(report.content_json, dict) else {}
            for index, link in enumerate(content.get("citation_links") or []):
                refs = normalize_citation_refs(link, index)
                if not refs:
                    continue
                # FC4: generic 引用（如 {"ref": finding_id}）也要解析出实际
                # 类型后再比对，复用 _resolve_generic_ref_type，不建第三份解析。
                for ref_type, ref_id, _p in refs:
                    actual_type = ref_type
                    if ref_type == "generic":
                        actual_type = await self._resolve_generic_ref_type(
                            session, case_id, ref_id
                        )
                    if actual_type == "finding" and ref_id == finding_id:
                        downstream.append(
                            {
                                "type": "report_document",
                                "id": str(report.id),
                                "relation": "cited_by",
                                "label": report.title,
                            }
                        )
                        break
        return downstream

    async def _resolve_generic_ref_type(
        self, session: Any, case_id: str, ref_id: str
    ) -> str | None:
        """generic 引用无类型：按 Evidence → Finding → Artifact 在 case 内判定。"""
        record = await session.get(EvidenceRecord, ref_id)
        if record is not None and record.case_id == case_id:
            return "evidence"
        record = await session.get(FindingRecord, ref_id)
        if record is not None and record.case_id == case_id:
            return "finding"
        record = await session.get(ArtifactRecord, ref_id)
        if record is not None and record.case_id == case_id:
            return "artifact"
        return None
