"""AgentDatabaseReadService: case-scoped structured database read surface.

本轮优化（V2）的核心目标：当用户询问"当前 Case 数据库现在实际有什么、多少、
是什么状态"时，Agent 必须能确定性查询当前数据库，而不是依赖 Conversation
History / Memory / RAG top-k 猜测。

本 Service 只做 orchestration：

    Case validation
    Input normalization
    Repository orchestration
    Pagination
    Cross-repository aggregation
    Field whitelist serialization
    Output bounding

禁止在本 Service 内直接 ``select(...)``；禁止 Tool handler 打开 session 或
直接访问 ORM。Repository 归属固定：

    Social Post / Comment / Social aggregate  -> SocialRepository
    CollectionRun                            -> CollectionRunRepository
    Finding                                  -> FindingRepository
    ReportDocument                           -> ReportDocumentRepository
    Case / Claim / Evidence / Artifact /
    Review / Activity                        -> ApplicationRepository

全部方法 case-scoped（DB-INV-3）；exact-ID 查询跨 Case 一律表现为
``found=False``（DB-INV-4）；输出经字段白名单，禁止 raw_payload /
embedding / content_hash（DB-INV-5）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from app.application.repositories import ApplicationRepository
from app.core.errors import ResourceNotFoundError
from app.infrastructure.database.collection_run_repository import CollectionRunRepository
from app.infrastructure.database.finding_repository import FindingRepository
from app.infrastructure.database.models import CaseRecord
from app.infrastructure.database.report_repository import ReportDocumentRepository
from app.infrastructure.database.social_repository import SocialRepository

# 输出体积上限（文档 §32/§35/§37）：避免单条超长文本耗尽 Agent Context。
_POST_LIST_CONTENT_LIMIT = 3000
_POST_SINGLE_CONTENT_LIMIT = 12000
_COMMENT_CONTENT_LIMIT = 2000

# 文档 §25: DB01–DB09 统一只读配置（cache 0 / no approval / parallel）。
_SORT_ORDER_LITERAL = Literal["newest", "oldest"]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _bounded_text(text: str, limit: int) -> tuple[str, bool]:
    """截断到 limit 字符，返回 (text, truncated)。"""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _not_found() -> dict[str, Any]:
    return {"ok": True, "found": False}


def _case_error() -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "database_record_not_found",
            "message": "Case not found for the runtime-injected case scope.",
        },
    }


class AgentDatabaseReadService:
    def __init__(
        self,
        *,
        repository: ApplicationRepository,
        social_repository: SocialRepository,
        collection_run_repository: CollectionRunRepository,
        finding_repository: FindingRepository,
        report_repository: ReportDocumentRepository,
    ) -> None:
        self._repository = repository
        self._social = social_repository
        self._collection_runs = collection_run_repository
        self._findings = finding_repository
        self._reports = report_repository

    async def _require_case(self, case_id: str) -> CaseRecord | None:
        try:
            return await self._repository.get_case(case_id)
        except ResourceNotFoundError:
            return None

    # ---------------- DB01 ----------------

    async def get_case_data_overview(self, *, case_id: str) -> dict[str, Any]:
        """DB01: 当前 Case 数据概况与精确数量（权威 exact count 来源）。"""
        case = await self._require_case(case_id)
        if case is None:
            return _case_error()

        posts = await self._social.count_posts(case_id)
        comments = await self._social.count_comments(case_id)
        posts_by_platform = [
            {"platform": platform, "count": count}
            for platform, count in await self._social.count_posts_by_platform(
                case_id
            )
        ]
        latest = await self._social.list_posts_page(
            case_id, sort_order="newest", limit=1
        )
        db_counts = await self._repository.get_case_database_counts(case_id)
        collection_runs = await self._collection_runs.count_for_case(case_id)
        findings = await self._findings.count(case_id)
        reports = await self._reports.count_for_case(case_id)
        active_runs = await self._collection_runs.list_active_for_case(case_id)

        time_range = case.time_range or {}
        return {
            "ok": True,
            "case": {
                "id": case.id,
                "title": case.title,
                "topic": case.topic,
                "status": case.status,
                "platforms": list(case.platforms or []),
                "time_range": {
                    "start": time_range.get("from"),
                    "end": time_range.get("to"),
                },
            },
            "counts": {
                "posts": posts,
                "comments": comments,
                "collection_runs": collection_runs,
                "claims": db_counts["claims"],
                "evidence": db_counts["evidence"],
                "artifacts": db_counts["artifacts"],
                "findings": findings,
                "review_items": db_counts["review_items"],
                "review_decisions": db_counts["review_decisions"],
                "reports": reports,
            },
            "posts_by_platform": posts_by_platform,
            "latest_post_published_at": (
                _iso(latest[0].published_at) if latest else None
            ),
            "active_collection_runs": [
                {
                    "id": run.id,
                    "phase": run.phase,
                    "status": run.status,
                    "posts_collected": run.posts_collected,
                    "comments_collected": run.comments_collected,
                    "updated_at": _iso(run.updated_at),
                }
                for run in active_runs
            ],
        }

    # ---------------- DB02 / DB03 / DB04 序列化 ----------------

    @staticmethod
    def _post_whitelist(
        record: Any,
        *,
        content_limit: int,
    ) -> dict[str, Any]:
        content, truncated = _bounded_text(record.content or "", content_limit)
        item: dict[str, Any] = {
            "id": record.id,
            "platform": record.platform,
            "native_id": record.native_id,
            "content_type": record.content_type,
            "title": record.title,
            "content": content,
            "author_id": record.author_id,
            "author_name": record.author_name,
            "source_url": record.source_url,
            "published_at": _iso(record.published_at),
            "engagement": dict(record.engagement or {}),
        }
        if truncated:
            item["content_truncated"] = True
        return item

    @staticmethod
    def _comment_whitelist(record: Any) -> dict[str, Any]:
        content, truncated = _bounded_text(
            record.content or "", _COMMENT_CONTENT_LIMIT
        )
        item: dict[str, Any] = {
            "id": record.id,
            "post_id": record.post_id,
            "platform": record.platform,
            "native_id": record.native_id,
            "parent_native_id": record.parent_native_id,
            "content": content,
            "author_id": record.author_id,
            "author_name": record.author_name,
            "published_at": _iso(record.published_at),
            "metrics": dict(record.metrics or {}),
        }
        if truncated:
            item["content_truncated"] = True
        return item

    def _next_offset(
        self, offset: int, returned: int, matched: int
    ) -> int | None:
        if offset + returned < matched:
            return offset + returned
        return None

    # ---------------- DB02 ----------------

    async def query_social_posts(
        self,
        *,
        case_id: str,
        platforms: list[str] | None = None,
        query: str | None = None,
        author: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        sort_order: _SORT_ORDER_LITERAL = "newest",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """DB02: 精确查询当前 Source Posts（lexical 过滤，非语义检索）。"""
        case = await self._require_case(case_id)
        if case is None:
            return _case_error()

        matched = await self._social.count_posts(
            case_id,
            platforms=platforms,
            q=query,
            author=author,
            date_from=date_from,
            date_to=date_to,
        )
        records = await self._social.list_posts_page(
            case_id,
            platforms=platforms,
            q=query,
            author=author,
            date_from=date_from,
            date_to=date_to,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
        posts = [
            self._post_whitelist(record, content_limit=_POST_LIST_CONTENT_LIMIT)
            for record in records
        ]
        return {
            "ok": True,
            "matched_count": matched,
            "returned_count": len(posts),
            "offset": offset,
            "next_offset": self._next_offset(offset, len(posts), matched),
            "posts": posts,
        }

    # ---------------- DB03 ----------------

    async def get_social_post(
        self,
        *,
        case_id: str,
        post_id: str | None = None,
        platform: str | None = None,
        native_id: str | None = None,
        include_comment_preview: bool = False,
        comment_preview_limit: int = 5,
    ) -> dict[str, Any]:
        """DB03: 通过稳定 ID 获取单条 Post（exact case scope）。"""
        case = await self._require_case(case_id)
        if case is None:
            return _case_error()

        record = await self._social.get_post_for_case(
            case_id,
            post_id=post_id,
            platform=platform,
            native_id=native_id,
        )
        if record is None:
            return {**_not_found(), "post": None}

        post = self._post_whitelist(
            record, content_limit=_POST_SINGLE_CONTENT_LIMIT
        )
        post["comment_count"] = await self._social.count_comments(
            case_id, post_id=record.id
        )
        if include_comment_preview and comment_preview_limit > 0:
            preview = await self._social.list_comments_page(
                case_id,
                post_id=record.id,
                sort_order="newest",
                limit=comment_preview_limit,
            )
            post["comment_preview"] = [
                self._comment_whitelist(item) for item in preview
            ]
        return {"ok": True, "found": True, "post": post}

    # ---------------- DB04 ----------------

    async def query_social_comments(
        self,
        *,
        case_id: str,
        post_id: str | None = None,
        platforms: list[str] | None = None,
        query: str | None = None,
        author: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        sort_order: _SORT_ORDER_LITERAL = "newest",
        limit: int = 30,
        offset: int = 0,
    ) -> dict[str, Any]:
        """DB04: 查询当前 Source Comments（经 SourcePost JOIN 保证 case scope）。"""
        case = await self._require_case(case_id)
        if case is None:
            return _case_error()

        matched = await self._social.count_comments(
            case_id,
            post_id=post_id,
            platforms=platforms,
            q=query,
            author=author,
            date_from=date_from,
            date_to=date_to,
        )
        records = await self._social.list_comments_page(
            case_id,
            post_id=post_id,
            platforms=platforms,
            q=query,
            author=author,
            date_from=date_from,
            date_to=date_to,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
        comments = [self._comment_whitelist(item) for item in records]
        return {
            "ok": True,
            "matched_count": matched,
            "returned_count": len(comments),
            "offset": offset,
            "next_offset": self._next_offset(offset, len(comments), matched),
            "comments": comments,
        }

    # ---------------- DB05 ----------------

    async def aggregate_social_data(
        self,
        *,
        case_id: str,
        group_by: Literal["platform", "day", "content_type"],
        platforms: list[str] | None = None,
        query: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """DB05: 精确 Post Count 聚合（platform / day / content_type）。

        day 聚合沿用 Python 侧按天聚合原则（双方言安全，不依赖 SQL 日期函数）。
        """
        case = await self._require_case(case_id)
        if case is None:
            return _case_error()

        if group_by == "platform":
            rows = await self._social.count_posts_by_platform(
                case_id,
                platforms=platforms,
                q=query,
                date_from=date_from,
                date_to=date_to,
            )
        elif group_by == "content_type":
            rows = await self._social.count_posts_by_content_type(
                case_id,
                platforms=platforms,
                q=query,
                date_from=date_from,
                date_to=date_to,
            )
        elif group_by == "day":
            rows = await self._social.list_post_time_rows(
                case_id,
                platforms=platforms,
                q=query,
                date_from=date_from,
                date_to=date_to,
            )
            by_day: dict[str, int] = {}
            for published_at, _platform in rows:
                if published_at is None:
                    day = "unknown"
                elif published_at.tzinfo is None:
                    # SQLite 读取后丢失时区：naive 直接取日历日期，避免
                    # astimezone 先假定本地时区再转 UTC 造成跨日偏移。
                    day = published_at.date().isoformat()
                else:
                    day = published_at.astimezone(UTC).date().isoformat()
                by_day[day] = by_day.get(day, 0) + 1
            rows = sorted(by_day.items())
        else:  # pragma: no cover - Literal 已约束
            return {
                "ok": False,
                "error": {
                    "code": "database_query_invalid",
                    "message": f"Unsupported group_by: {group_by}",
                },
            }

        total = await self._social.count_posts(
            case_id,
            platforms=platforms,
            q=query,
            date_from=date_from,
            date_to=date_to,
        )
        buckets = [
            {"key": str(key), "count": int(count)} for key, count in rows
        ][:limit]
        return {
            "ok": True,
            "metric": "post_count",
            "group_by": group_by,
            "total": total,
            "buckets": buckets,
        }

    # ---------------- DB06 ----------------

    @staticmethod
    def _finding_whitelist(record: Any) -> dict[str, Any]:
        return {
            "id": record.id,
            "kind": record.kind,
            "title": record.title,
            "statement": record.statement,
            "status": record.status,
            "confidence": record.confidence,
            "attributes": dict(record.attributes_json or {}),
            "source_run_id": record.source_run_id,
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
        }

    async def query_findings(
        self,
        *,
        case_id: str,
        finding_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        query: str | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> dict[str, Any]:
        """DB06: 查询当前 Finding 状态（exact 模式附带 evidence/source links）。"""
        case = await self._require_case(case_id)
        if case is None:
            return _case_error()

        if finding_id is not None:
            records = await self._findings.list(
                case_id, finding_id=finding_id, limit=1
            )
            if not records:
                return {"ok": True, "found": False, "findings": []}
            finding = self._finding_whitelist(records[0])
            finding["evidence_links"] = [
                {
                    "evidence_ref": link.evidence_ref,
                    "relation": link.relation,
                }
                for link in await self._findings.list_evidence_links(
                    records[0].id
                )
            ]
            finding["source_links"] = [
                {
                    "source_type": link.source_type,
                    "source_id": link.source_id,
                    "source_path": link.source_path,
                }
                for link in await self._findings.list_source_links(records[0].id)
            ]
            return {"ok": True, "found": True, "findings": [finding]}

        matched = await self._findings.count(
            case_id, kind=kind, status=status, query=query
        )
        records = await self._findings.list(
            case_id,
            kind=kind,
            status=status,
            query=query,
            limit=limit,
            offset=offset,
        )
        findings = [self._finding_whitelist(item) for item in records]
        return {
            "ok": True,
            "matched_count": matched,
            "returned_count": len(findings),
            "offset": offset,
            "next_offset": self._next_offset(offset, len(findings), matched),
            "findings": findings,
        }

    # ---------------- DB07 ----------------

    @staticmethod
    def _review_item_whitelist(record: Any) -> dict[str, Any]:
        return {
            "id": record.id,
            "object_type": record.object_type,
            "object_id": record.object_id,
            "priority": record.priority,
            "status": record.status,
            "risk_level": record.risk_level,
            "queue": record.queue,
            "current_version": record.current_version,
            "summary": record.summary,
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
        }

    async def _latest_review_decision(
        self, item_id: str
    ) -> dict[str, Any] | None:
        decisions = await self._repository.list_review_decisions(
            item_id, limit=1
        )
        if not decisions:
            return None
        decision = decisions[0]
        return {
            "id": decision.id,
            "object_version": decision.object_version,
            "decision": decision.decision,
            "reason": decision.reason,
            "actor": decision.actor,
            "supersedes_id": decision.supersedes_id,
            "created_at": _iso(decision.created_at),
        }

    async def query_review_items(
        self,
        *,
        case_id: str,
        review_item_id: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        status: str | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> dict[str, Any]:
        """DB07: 查询 Human Review 当前状态（只读，exact 模式附 latest_decision）。"""
        case = await self._require_case(case_id)
        if case is None:
            return _case_error()

        exact = review_item_id is not None or (
            bool(object_type) and bool(object_id)
        )
        records = await self._repository.list_review_items(
            case_id,
            review_item_id=review_item_id,
            object_type=object_type,
            object_id=object_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        items = [self._review_item_whitelist(item) for item in records]

        if exact:
            if not items:
                return {"ok": True, "found": False, "review_items": []}
            # exact 模式只对首个命中附加 latest_decision（避免列表 N+1）。
            items[0]["latest_decision"] = await self._latest_review_decision(
                records[0].id
            )
            return {
                "ok": True,
                "found": True,
                "matched_count": 1,
                "review_items": items,
            }

        matched = len(
            await self._repository.list_review_items(
                case_id,
                object_type=object_type,
                object_id=object_id,
                status=status,
                limit=limit + 1,
            )
        )
        return {
            "ok": True,
            "matched_count": matched,
            "returned_count": len(items),
            "offset": offset,
            "next_offset": self._next_offset(offset, len(items), matched),
            "review_items": items,
        }

    # ---------------- DB08 ----------------

    @staticmethod
    def _report_whitelist(record: Any) -> dict[str, Any]:
        return {
            "id": record.id,
            "family_id": record.family_id,
            "source_artifact_id": record.source_artifact_id,
            "supersedes_id": record.supersedes_id,
            "status": record.status,
            "title": record.title,
            "lock_version": record.lock_version,
            "published_at": _iso(record.published_at),
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
        }

    @staticmethod
    def _report_content_preview(record: Any) -> dict[str, Any]:
        content = record.content_json or {}
        sections = content.get("sections") or []
        section_titles = [
            str(section.get("title", ""))
            for section in sections
            if isinstance(section, dict) and section.get("title")
        ]
        citation_links = content.get("citation_links") or []
        return {
            "executive_summary": str(content.get("executive_summary", "")),
            "section_titles": section_titles,
            "citation_count": len(citation_links),
        }

    async def query_reports(
        self,
        *,
        case_id: str,
        report_id: str | None = None,
        status: str | None = None,
        include_content_preview: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """DB08: 查询当前 ReportDocument（exact scope；默认不含完整 content）。"""
        case = await self._require_case(case_id)
        if case is None:
            return _case_error()

        if report_id is not None:
            records = await self._reports.list_for_case(
                case_id, report_id=report_id, limit=1
            )
            if not records:
                return {"ok": True, "found": False, "reports": []}
            report = self._report_whitelist(records[0])
            if include_content_preview:
                report["content_preview"] = self._report_content_preview(
                    records[0]
                )
            return {"ok": True, "found": True, "reports": [report]}

        matched = await self._reports.count_for_case(case_id, status=status)
        records = await self._reports.list_for_case(
            case_id, status=status, limit=limit, offset=offset
        )
        reports = [self._report_whitelist(item) for item in records]
        if include_content_preview:
            for report, record in zip(reports, records, strict=True):
                report["content_preview"] = self._report_content_preview(record)
        return {
            "ok": True,
            "matched_count": matched,
            "returned_count": len(reports),
            "offset": offset,
            "next_offset": self._next_offset(offset, len(reports), matched),
            "reports": reports,
        }

    # ---------------- DB09 ----------------

    async def query_case_activity(
        self,
        *,
        case_id: str,
        activity_type: str | None = None,
        actor: str | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> dict[str, Any]:
        """DB09: 查询 Case Activity 日志（默认不返回 metadata_json）。"""
        case = await self._require_case(case_id)
        if case is None:
            return _case_error()

        records = await self._repository.list_activity_log(
            case_id,
            activity_type=activity_type,
            actor=actor,
            limit=limit,
            offset=offset,
        )
        items = [
            {
                "id": record.id,
                "activity_type": record.activity_type,
                "summary": record.summary,
                "actor": record.actor,
                "ref_run_id": record.ref_run_id,
                "ref_tool_call_id": record.ref_tool_call_id,
                "created_at": _iso(record.created_at),
            }
            for record in records
        ]
        return {
            "ok": True,
            "returned_count": len(items),
            "offset": offset,
            "next_offset": offset + len(items) if items else None,
            "items": items,
        }
