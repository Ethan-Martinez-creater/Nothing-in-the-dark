"""M7: Report Document service — import/edit/publish gate/revise。

Publish Gate 为确定性校验（不做同步 LLM 调用）；引用校验复用 Evidence/
Finding/Artifact 的 case 内解析，跨 case 引用一律阻止。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.errors import ApplicationError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    ArtifactRecord,
    CaseRecord,
    EvidenceRecord,
    ReportDocumentRecord,
)
from app.infrastructure.database.report_repository import ReportDocumentRepository

_REPORT_STATUS_TRANSITIONS = {
    ("draft", "in_review"),
    ("in_review", "draft"),
    ("in_review", "published"),
    ("draft", "published"),
    ("published", "archived"),
}


class ReportDocumentService:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._reports = ReportDocumentRepository(database)

    # ---------------- import ----------------

    async def import_from_artifact(
        self, case_id: str, artifact_id: str
    ) -> ReportDocumentRecord:
        async with self._database.session_factory() as session:
            artifact = await session.get(ArtifactRecord, artifact_id)
            if artifact is None or artifact.case_id != case_id:
                raise ApplicationError(
                    "report artifact not found in this case",
                    code="report_scope_mismatch",
                )
            if artifact.kind != "report":
                raise ApplicationError(
                    f"artifact '{artifact_id}' is not a report",
                    code="report_not_found",
                )
            case = await session.get(CaseRecord, case_id)
            case_title = case.title if case else case_id

        existing = await self._reports.latest_for_artifact(artifact_id)
        if existing is not None and existing.status in ("draft", "in_review"):
            # 幂等：同一 artifact 已有可编辑草稿时直接返回
            return existing

        data = artifact.data if isinstance(artifact.data, dict) else {}
        content = {
            "title": str(data.get("title") or artifact.title),
            "executive_summary": str(data.get("summary") or data.get("executive_summary") or ""),
            "sections": list(data.get("sections") or []),
            "citation_links": list(data.get("citation_links") or data.get("references") or []),
            "disclaimer": "本报告由系统辅助生成，结论以人工审核为准。",
        }
        record = ReportDocumentRecord(
            family_id=str(uuid.uuid4()),
            case_id=case_id,
            source_artifact_id=artifact_id,
            status="draft",
            title=f"{case_title} · 调查报告",
            content_json=content,
            lock_version=1,
        )
        return await self._reports.create(record)

    # ---------------- 查询 ----------------

    async def get_by_id(self, report_id: str) -> ReportDocumentRecord | None:
        return await self._reports.get(report_id)

    async def get_for_case(self, case_id: str, report_id: str) -> ReportDocumentRecord:
        record = await self._reports.get(report_id)
        if record is None:
            raise ApplicationError(
                f"report '{report_id}' does not exist", code="report_not_found"
            )
        if record.case_id != case_id:
            raise ApplicationError(
                "report belongs to another case", code="report_scope_mismatch"
            )
        return record

    async def list_for_case(self, case_id: str) -> list[ReportDocumentRecord]:
        return list(await self._reports.list_for_case(case_id))

    async def list_global(
        self, *, status: str | None = None
    ) -> list[ReportDocumentRecord]:
        return list(await self._reports.list_global(status=status))

    # ---------------- edit ----------------

    async def update_draft(
        self,
        case_id: str,
        report_id: str,
        *,
        expected_lock_version: int,
        title: str | None = None,
        content: dict[str, Any] | None = None,
    ) -> ReportDocumentRecord:
        record = await self.get_for_case(case_id, report_id)
        if record.status not in ("draft", "in_review"):
            raise ApplicationError(
                f"report '{report_id}' is {record.status} and read-only",
                code="report_invalid_transition",
            )
        if title is not None and not title.strip():
            raise ApplicationError(
                "report title must not be empty", code="report_validation_failed"
            )
        updated = await self._reports.update_draft(
            report_id,
            expected_lock_version=expected_lock_version,
            title=title,
            content_json=content,
        )
        if updated is None:
            raise ApplicationError(
                "report was modified by someone else; reload latest version",
                code="report_version_conflict",
            )
        return updated

    # ---------------- 状态机 ----------------

    async def change_status(
        self, case_id: str, report_id: str, status: str
    ) -> ReportDocumentRecord:
        record = await self.get_for_case(case_id, report_id)
        transition = (record.status, status)
        if transition not in _REPORT_STATUS_TRANSITIONS:
            raise ApplicationError(
                f"invalid report transition {record.status} -> {status}",
                code="report_invalid_transition",
            )
        if status == "published":
            await self._publish_gate(record)
        updated = await self._reports.change_status(
            report_id,
            expected_lock_version=record.lock_version,
            status=status,
            published_at=datetime.now(UTC) if status == "published" else None,
        )
        if updated is None:
            raise ApplicationError(
                "report changed concurrently; reload and retry",
                code="report_version_conflict",
            )
        return updated

    # ---------------- publish gate（确定性） ----------------

    async def _publish_gate(self, record: ReportDocumentRecord) -> None:
        problems: list[dict[str, str]] = []
        if not record.title.strip():
            problems.append({"field": "title", "issue": "title_is_empty"})
        content = record.content_json if isinstance(record.content_json, dict) else {}
        summary = str(content.get("executive_summary") or "").strip()
        sections = content.get("sections") or []
        if not summary and not sections:
            problems.append(
                {"field": "content", "issue": "executive_summary_or_sections_required"}
            )

        citation_links = content.get("citation_links") or []
        for index, link in enumerate(citation_links):
            ref = self._normalize_citation(link)
            if ref is None:
                problems.append(
                    {"field": f"citation_links[{index}]", "issue": "unresolvable_ref"}
                )
                continue
            try:
                evidence_id = self._evidence_id_from_ref(ref)
                if evidence_id is None:
                    continue
                async with self._database.session_factory() as session:
                    evidence = await session.get(EvidenceRecord, evidence_id)
                if evidence is None or evidence.case_id != record.case_id:
                    problems.append(
                        {
                            "field": f"citation_links[{index}]",
                            "issue": "evidence_not_in_case",
                        }
                    )
            except Exception:  # noqa: BLE001 - 引用解析失败按无效处理
                problems.append(
                    {"field": f"citation_links[{index}]", "issue": "unresolvable_ref"}
                )

        if problems:
            raise ApplicationError(
                "report publish validation failed",
                code="report_publish_validation_failed",
            )

    def _normalize_citation(self, link: Any) -> dict[str, Any] | None:
        if isinstance(link, dict):
            return link
        if isinstance(link, str):
            return {"ref": link}
        return None

    def _evidence_id_from_ref(self, link: dict[str, Any]) -> str | None:
        """从引用块解析 Evidence ID（ev-xxx / ev_xxx 前缀约定）。"""
        for key in ("evidence_id", "evidence", "ref", "id"):
            value = link.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                for prefix in ("ev-", "ev_", "evidence-", "evidence_"):
                    if text.lower().startswith(prefix):
                        return text
                return text if text.lower().startswith("ev") else None
        return None

    # ---------------- revise ----------------

    async def revise(
        self, case_id: str, report_id: str
    ) -> ReportDocumentRecord:
        record = await self.get_for_case(case_id, report_id)
        if record.status == "draft":
            raise ApplicationError(
                "draft reports are edited in place; no revision needed",
                code="report_invalid_transition",
            )
        new_record = ReportDocumentRecord(
            family_id=record.family_id,
            case_id=record.case_id,
            source_artifact_id=record.source_artifact_id,
            supersedes_id=record.id,
            status="draft",
            title=record.title,
            content_json=dict(record.content_json or {}),
            lock_version=1,
        )
        return await self._reports.create(new_record)
