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
    FindingRecord,
    ReportDocumentRecord,
    SourceCommentRecord,
    SourcePostRecord,
)
from app.infrastructure.database.report_repository import ReportDocumentRepository
from sqlalchemy import select

_REPORT_STATUS_TRANSITIONS = {
    ("draft", "in_review"),
    ("in_review", "draft"),
    ("in_review", "published"),
    ("draft", "published"),
    ("published", "archived"),
}


def normalize_citation_refs(link: Any, index: int = 0) -> list[tuple[str, str, str]] | None:
    """把单个 citation link 归一化为 (type, id, path) 引用列表。

    支持：字符串、evidence(_id)(_ids)、finding(_id)(_ids)、
    artifact(_id)(_ids)、generic ref/id。generic 无类型时按
    Evidence → Finding → Artifact 顺序在当前 case 内解析。
    无法提取任何引用时返回 None（unknown shape）。

    模块级函数：Provenance（C5）复用同一 parser，不维护第二份。
    """
    base = f"citation_links[{index}]"
    if isinstance(link, str):
        text = link.strip()
        return [("generic", text, base)] if text else None
    if not isinstance(link, dict):
        return None

    refs: list[tuple[str, str, str]] = []

    def _collect(ref_type: str, value: Any, path: str) -> None:
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if text.startswith("social_post:"):
                # 报告生成器引用帖子时使用 social_post:<id> 前缀；这类引用
                # 指向 SourcePost（帖子），不是 Evidence，需单独解析。
                refs.append(("social_post", text[len("social_post:"):], path))
            elif text.startswith("social_comment:"):
                refs.append(("social_comment", text[len("social_comment:"):], path))
            elif text.startswith("aggregate_social_data:"):
                # 聚合引用（aggregate_social_data:<group_by>）：报告对统计结论
                # 的依据引用，指向确定性聚合结果而非某个对象 id。
                refs.append(
                    (
                        "aggregate_social_data",
                        text[len("aggregate_social_data:"):],
                        path,
                    )
                )
            else:
                refs.append((ref_type, text, path))

    for key in ("evidence_id", "evidence"):
        _collect("evidence", link.get(key), f"{base}.{key}")
    evidence_ids = link.get("evidence_ids")
    if isinstance(evidence_ids, list):
        for j, value in enumerate(evidence_ids):
            _collect("evidence", value, f"{base}.evidence_ids[{j}]")
    for key in ("finding_id", "finding"):
        _collect("finding", link.get(key), f"{base}.{key}")
    finding_ids = link.get("finding_ids")
    if isinstance(finding_ids, list):
        for j, value in enumerate(finding_ids):
            _collect("finding", value, f"{base}.finding_ids[{j}]")
    for key in ("artifact_id", "artifact"):
        _collect("artifact", link.get(key), f"{base}.{key}")
    artifact_ids = link.get("artifact_ids")
    if isinstance(artifact_ids, list):
        for j, value in enumerate(artifact_ids):
            _collect("artifact", value, f"{base}.artifact_ids[{j}]")
    for key in ("ref", "id"):
        _collect("generic", link.get(key), f"{base}.{key}")

    return refs or None


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
        problems.extend(await self._validate_citations(record.case_id, citation_links))

        if problems:
            raise ApplicationError(
                "report publish validation failed",
                code="report_publish_validation_failed",
                details=problems,
            )

    async def _validate_citations(
        self, case_id: str, citation_links: list[Any]
    ) -> list[dict[str, str]]:
        """逐条解析 citation 引用（C3）：每个引用必须真实存在且属于当前 case。"""
        problems: list[dict[str, str]] = []
        for index, link in enumerate(citation_links):
            refs = normalize_citation_refs(link, index)
            if refs is None:
                # unknown shape：没有任何可解析引用，fail closed
                problems.append(
                    {
                        "field": f"citation_links[{index}]",
                        "issue": "unresolvable_ref",
                    }
                )
                continue
            for ref_type, ref_id, path in refs:
                problem = await self._citation_ref_problem(case_id, ref_type, ref_id)
                if problem is not None:
                    problems.append({"field": path, "issue": problem})
        return problems

    async def _citation_ref_problem(
        self, case_id: str, ref_type: str, ref_id: str
    ) -> str | None:
        """返回问题 issue 名或 None；只认数据库记录，不靠 ID 前缀。"""
        async with self._database.session_factory() as session:
            if ref_type == "aggregate_social_data":
                # 聚合引用（aggregate_social_data:<group_by>）：确定性统计结论
                # 的依据，非对象 id；只校验 group_by 是否为工具的合法取值。
                if ref_id in ("platform", "day", "content_type"):
                    return None
                return "unresolvable_ref"
            if ref_type == "social_post":
                # 帖子引用（social_post:<id>）：报告生成器可能写 uuid 前 8 位
                # 短 id，先精确匹配完整主键，再用前缀 LIKE 兼容短 id。
                record = await session.get(SourcePostRecord, ref_id)
                if record is None:
                    record = await session.scalar(
                        select(SourcePostRecord).where(
                            SourcePostRecord.id.like(f"{ref_id}%")
                        )
                    )
                if record is not None:
                    if record.case_id != case_id:
                        return "post_not_in_case"
                    return None
                return "post_not_found"
            if ref_type == "social_comment":
                # 评论引用（social_comment:<id>）：评论无 case_id，必须 JOIN
                # 其 SourcePost 才能校验 Case scope。
                record = await session.scalar(
                    select(SourceCommentRecord).where(
                        SourceCommentRecord.id.like(f"{ref_id}%")
                    )
                )
                if record is not None:
                    post = await session.get(SourcePostRecord, record.post_id)
                    if post is not None and post.case_id == case_id:
                        return None
                    return "comment_not_in_case"
                return "comment_not_found"
            if ref_type in ("evidence", "generic"):
                record = await session.get(EvidenceRecord, ref_id)
                if record is not None:
                    if record.case_id != case_id:
                        return "evidence_not_in_case"
                    return None
                if ref_type == "evidence":
                    # 报告生成器对帖子/评论有时直接写裸 uuid（完整 id）而漏掉
                    # social_post:/social_comment: 前缀；Evidence 查不到时按
                    # 帖子、评论依次精确匹配。
                    post = await session.scalar(
                        select(SourcePostRecord).where(
                            SourcePostRecord.id == ref_id
                        )
                    )
                    if post is not None:
                        if post.case_id != case_id:
                            return "post_not_in_case"
                        return None
                    comment = await session.scalar(
                        select(SourceCommentRecord).where(
                            SourceCommentRecord.id == ref_id
                        )
                    )
                    if comment is not None:
                        owner = await session.get(
                            SourcePostRecord, comment.post_id
                        )
                        if owner is not None and owner.case_id == case_id:
                            return None
                        return "comment_not_in_case"
                    return "evidence_not_found"
            if ref_type in ("finding", "generic"):
                record = await session.get(FindingRecord, ref_id)
                if record is not None:
                    if record.case_id != case_id:
                        return "finding_not_in_case"
                    return None
                if ref_type == "finding":
                    return "finding_not_found"
            if ref_type in ("artifact", "generic"):
                record = await session.get(ArtifactRecord, ref_id)
                if record is not None:
                    if record.case_id != case_id:
                        return "artifact_not_in_case"
                    return None
                if ref_type == "artifact":
                    return "artifact_not_found"
        return "unresolvable_ref"

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
