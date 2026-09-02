"""DB01–DB09: 结构化数据库查询 Tool Pack（V2 最终实施规格）。

这些 Tool 让 Agent 能够确定性查询"当前 Case 数据库真实状态"（exact count /
exact list / current status），替代从 Conversation History、Memory 或 RAG
top-k 中猜测。

设计约束（文档 §4/§5/§25/§62）：
- 全部只读：permissions=("read_database",)，side_effect="none"。
- 实时 Tool 不缓存：cache_ttl_seconds=0（CollectionRun 可能持续增量写库）。
- case_id 由 Runtime 注入（文档 §53），模型不得自由构造。
- Tool handler 不打开 session、不直接访问 ORM、不拼 SQL。
- 输出经字段白名单（禁止 raw_payload / embedding / content_hash）。
- exact-ID 查询跨 Case 一律表现为 found=False（DB-INV-4）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.application.agent_database_service import AgentDatabaseReadService
from app.harness.tools import ToolRegistry, ToolSpec

# 所有 DB Tool 统一 ToolSpec 配置（文档 §25）。
_DB_TOOL_CONFIG: dict[str, Any] = {
    "version": "1.0.0",
    "permissions": ("read_database",),
    "side_effect": "none",
    "idempotent": True,
    "requires_approval": False,
    "execution_mode": "parallel",
    "cache_ttl_seconds": 0,
    "max_concurrency": 8,
    "timeout_seconds": 10,
    "max_retries": 0,
    "execution_class": "trusted_in_process",
    "filesystem": {},
    "network": {},
    "secrets": (),
    "risk_level": "low",
}

_UNAVAILABLE = {
    "ok": False,
    "error": {
        "code": "database_query_unavailable",
        "message": "Database query service is not configured.",
    },
}


# ---------------------------------------------------------------------------
# Input Models（文档 §26/§29/§33/§36/§38/§40/§44/§48/§51）
# ---------------------------------------------------------------------------


class CaseDataOverviewInput(BaseModel):
    case_id: str | None = Field(
        default=None,
        description="Injected by runtime; never model-controlled.",
    )


class QuerySocialPostsInput(BaseModel):
    case_id: str | None = Field(
        default=None,
        description="Injected by runtime; never model-controlled.",
    )
    platforms: list[str] | None = Field(
        default=None,
        description=(
            "Optional platform filters such as weibo, bilibili, douyin, zhihu, "
            "or tieba. Omit to query all platforms available in the current case."
        ),
    )
    query: str | None = Field(
        default=None,
        max_length=300,
        description=(
            "Optional lexical substring filter over persisted post text. "
            "This is deterministic database filtering, not semantic search."
        ),
    )
    author: str | None = Field(
        default=None,
        max_length=200,
        description="Optional lexical filter over author name or author id.",
    )
    date_from: datetime | None = Field(
        default=None,
        description="Inclusive lower bound on published_at (ISO-8601).",
    )
    date_to: datetime | None = Field(
        default=None,
        description="Inclusive upper bound on published_at (ISO-8601).",
    )
    sort_order: Literal["newest", "oldest"] = Field(
        default="newest",
        description="Ordering of persisted records by published_at.",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of records to return in this call.",
    )
    offset: int = Field(
        default=0,
        ge=0,
        le=5000,
        description="Number of records to skip for pagination.",
    )


class GetSocialPostInput(BaseModel):
    case_id: str | None = Field(
        default=None,
        description="Injected by runtime; never model-controlled.",
    )
    post_id: str | None = Field(
        default=None,
        description="Stable database id of the post to fetch exactly.",
    )
    platform: str | None = Field(
        default=None,
        description="Platform of the post when identifying by platform + native_id.",
    )
    native_id: str | None = Field(
        default=None,
        description="Native platform id of the post when identifying by platform + native_id.",
    )
    include_comment_preview: bool = Field(
        default=False,
        description="When true, include the newest comments of the post.",
    )
    comment_preview_limit: int = Field(
        default=5,
        ge=0,
        le=20,
        description="Maximum comments to include in the preview.",
    )


class QuerySocialCommentsInput(BaseModel):
    case_id: str | None = Field(
        default=None,
        description="Injected by runtime; never model-controlled.",
    )
    post_id: str | None = Field(
        default=None,
        description="Restrict comments to a single persisted post id.",
    )
    platforms: list[str] | None = Field(
        default=None,
        description=(
            "Optional platform filters. Omit to query all platforms available "
            "in the current case."
        ),
    )
    query: str | None = Field(
        default=None,
        max_length=300,
        description=(
            "Optional lexical substring filter over comment text. "
            "This is deterministic database filtering, not semantic search."
        ),
    )
    author: str | None = Field(
        default=None,
        max_length=200,
        description="Optional lexical filter over comment author name or id.",
    )
    date_from: datetime | None = Field(
        default=None,
        description="Inclusive lower bound on comment published_at.",
    )
    date_to: datetime | None = Field(
        default=None,
        description="Inclusive upper bound on comment published_at.",
    )
    sort_order: Literal["newest", "oldest"] = Field(
        default="newest",
        description="Ordering of persisted comments by published_at.",
    )
    limit: int = Field(
        default=30,
        ge=1,
        le=100,
        description="Maximum number of comments to return in this call.",
    )
    offset: int = Field(
        default=0,
        ge=0,
        le=5000,
        description="Number of comments to skip for pagination.",
    )


class AggregateSocialDataInput(BaseModel):
    case_id: str | None = Field(
        default=None,
        description="Injected by runtime; never model-controlled.",
    )
    group_by: Literal["platform", "day", "content_type"] = Field(
        description=(
            "How to group the exact post-count aggregation: platform, day "
            "(calendar day), or content_type."
        ),
    )
    platforms: list[str] | None = Field(
        default=None,
        description="Optional platform filters to apply before aggregating.",
    )
    query: str | None = Field(
        default=None,
        max_length=300,
        description="Optional lexical substring filter applied before aggregating.",
    )
    date_from: datetime | None = Field(
        default=None,
        description="Inclusive lower bound on published_at.",
    )
    date_to: datetime | None = Field(
        default=None,
        description="Inclusive upper bound on published_at.",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Maximum number of buckets to return.",
    )


class QueryFindingsInput(BaseModel):
    case_id: str | None = Field(
        default=None,
        description="Injected by runtime; never model-controlled.",
    )
    finding_id: str | None = Field(
        default=None,
        description="Stable finding id for an exact lookup (adds evidence/source links).",
    )
    kind: str | None = Field(
        default=None,
        description="Filter by finding kind, e.g. opinion_analysis, fact_check.",
    )
    status: str | None = Field(
        default=None,
        description=(
            "Filter by workflow status: candidate, under_review, verified, "
            "rejected, or superseded. Only verified findings are "
            "Human-Review-accepted conclusions."
        ),
    )
    query: str | None = Field(
        default=None,
        max_length=300,
        description="Optional lexical filter over finding title or statement.",
    )
    limit: int = Field(
        default=30,
        ge=1,
        le=100,
        description="Maximum number of findings to return.",
    )
    offset: int = Field(
        default=0,
        ge=0,
        le=5000,
        description="Number of findings to skip for pagination.",
    )


class QueryReviewItemsInput(BaseModel):
    case_id: str | None = Field(
        default=None,
        description="Injected by runtime; never model-controlled.",
    )
    review_item_id: str | None = Field(
        default=None,
        description="Stable review item id for an exact lookup (adds latest_decision).",
    )
    object_type: str | None = Field(
        default=None,
        description="Object type, e.g. finding, evidence, claim, propagation_edge.",
    )
    object_id: str | None = Field(
        default=None,
        description="Object id; pair with object_type for an exact object lookup.",
    )
    status: str | None = Field(
        default=None,
        description="Filter by review status, e.g. unreviewed, in_review, accepted, rejected.",
    )
    limit: int = Field(
        default=30,
        ge=1,
        le=100,
        description="Maximum number of review items to return.",
    )
    offset: int = Field(
        default=0,
        ge=0,
        le=5000,
        description="Number of review items to skip for pagination.",
    )


class QueryReportsInput(BaseModel):
    case_id: str | None = Field(
        default=None,
        description="Injected by runtime; never model-controlled.",
    )
    report_id: str | None = Field(
        default=None,
        description="Stable report document id for an exact lookup.",
    )
    status: str | None = Field(
        default=None,
        description="Filter by publication status, e.g. draft, in_review, published, archived.",
    )
    include_content_preview: bool = Field(
        default=False,
        description=(
            "When true, include a bounded content preview (executive summary, "
            "section titles, citation count) instead of the full content."
        ),
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of reports to return.",
    )
    offset: int = Field(
        default=0,
        ge=0,
        le=5000,
        description="Number of reports to skip for pagination.",
    )


class QueryCaseActivityInput(BaseModel):
    case_id: str | None = Field(
        default=None,
        description="Injected by runtime; never model-controlled.",
    )
    activity_type: str | None = Field(
        default=None,
        description="Filter by activity_type, e.g. case_created, collection_started.",
    )
    actor: str | None = Field(
        default=None,
        max_length=100,
        description="Filter by the actor that performed the activity.",
    )
    limit: int = Field(
        default=30,
        ge=1,
        le=100,
        description="Maximum number of activity records to return.",
    )
    offset: int = Field(
        default=0,
        ge=0,
        le=5000,
        description="Number of activity records to skip for pagination.",
    )


# ---------------------------------------------------------------------------
# Output Models（宽松白名单验证；extra allow 以承载错误分支与分页字段）
# ---------------------------------------------------------------------------


class CaseDataOverviewOutput(BaseModel):
    ok: bool
    case: dict[str, Any] | None = None
    counts: dict[str, int] | None = None
    posts_by_platform: list[dict[str, Any]] | None = None
    latest_post_published_at: str | None = None
    active_collection_runs: list[dict[str, Any]] | None = None
    error: dict[str, Any] | None = None
    model_config = {"extra": "allow"}


class QuerySocialPostsOutput(BaseModel):
    ok: bool
    matched_count: int = 0
    returned_count: int = 0
    offset: int = 0
    next_offset: int | None = None
    posts: list[dict[str, Any]] = []
    error: dict[str, Any] | None = None
    model_config = {"extra": "allow"}


class GetSocialPostOutput(BaseModel):
    ok: bool
    found: bool = False
    post: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    model_config = {"extra": "allow"}


class QuerySocialCommentsOutput(BaseModel):
    ok: bool
    matched_count: int = 0
    returned_count: int = 0
    offset: int = 0
    next_offset: int | None = None
    comments: list[dict[str, Any]] = []
    error: dict[str, Any] | None = None
    model_config = {"extra": "allow"}


class AggregateSocialDataOutput(BaseModel):
    ok: bool
    metric: str = "post_count"
    group_by: str | None = None
    total: int = 0
    buckets: list[dict[str, Any]] = []
    error: dict[str, Any] | None = None
    model_config = {"extra": "allow"}


class QueryFindingsOutput(BaseModel):
    ok: bool
    found: bool | None = None
    matched_count: int = 0
    returned_count: int = 0
    offset: int = 0
    next_offset: int | None = None
    findings: list[dict[str, Any]] = []
    error: dict[str, Any] | None = None
    model_config = {"extra": "allow"}


class QueryReviewItemsOutput(BaseModel):
    ok: bool
    found: bool | None = None
    matched_count: int = 0
    returned_count: int = 0
    offset: int = 0
    next_offset: int | None = None
    review_items: list[dict[str, Any]] = []
    error: dict[str, Any] | None = None
    model_config = {"extra": "allow"}


class QueryReportsOutput(BaseModel):
    ok: bool
    found: bool | None = None
    matched_count: int = 0
    returned_count: int = 0
    offset: int = 0
    next_offset: int | None = None
    reports: list[dict[str, Any]] = []
    error: dict[str, Any] | None = None
    model_config = {"extra": "allow"}


class QueryCaseActivityOutput(BaseModel):
    ok: bool
    returned_count: int = 0
    offset: int = 0
    next_offset: int | None = None
    items: list[dict[str, Any]] = []
    error: dict[str, Any] | None = None
    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Tool handlers（文档 §61：仅 orchestrate，不触碰 session/ORM/SQL）
# ---------------------------------------------------------------------------


def _check(service: AgentDatabaseReadService | None, case_id: str | None) -> bool:
    """Return True when the call is executable; else already returned error."""
    if service is None or not case_id:
        return False
    return True


def register_database_tools(
    registry: ToolRegistry,
    service: AgentDatabaseReadService | None,
) -> None:
    """Register DB01–DB09 (idempotent; duplicate names are skipped)."""

    async def get_case_data_overview(arguments: BaseModel) -> dict[str, Any]:
        request = CaseDataOverviewInput.model_validate(arguments)
        if not _check(service, request.case_id):
            return dict(_UNAVAILABLE)
        return await service.get_case_data_overview(case_id=request.case_id)

    async def query_social_posts(arguments: BaseModel) -> dict[str, Any]:
        request = QuerySocialPostsInput.model_validate(arguments)
        if not _check(service, request.case_id):
            return dict(_UNAVAILABLE)
        return await service.query_social_posts(
            case_id=request.case_id,
            platforms=request.platforms,
            query=request.query,
            author=request.author,
            date_from=request.date_from,
            date_to=request.date_to,
            sort_order=request.sort_order,
            limit=request.limit,
            offset=request.offset,
        )

    async def get_social_post(arguments: BaseModel) -> dict[str, Any]:
        request = GetSocialPostInput.model_validate(arguments)
        if not _check(service, request.case_id):
            return dict(_UNAVAILABLE)
        return await service.get_social_post(
            case_id=request.case_id,
            post_id=request.post_id,
            platform=request.platform,
            native_id=request.native_id,
            include_comment_preview=request.include_comment_preview,
            comment_preview_limit=request.comment_preview_limit,
        )

    async def query_social_comments(arguments: BaseModel) -> dict[str, Any]:
        request = QuerySocialCommentsInput.model_validate(arguments)
        if not _check(service, request.case_id):
            return dict(_UNAVAILABLE)
        return await service.query_social_comments(
            case_id=request.case_id,
            post_id=request.post_id,
            platforms=request.platforms,
            query=request.query,
            author=request.author,
            date_from=request.date_from,
            date_to=request.date_to,
            sort_order=request.sort_order,
            limit=request.limit,
            offset=request.offset,
        )

    async def aggregate_social_data(arguments: BaseModel) -> dict[str, Any]:
        request = AggregateSocialDataInput.model_validate(arguments)
        if not _check(service, request.case_id):
            return dict(_UNAVAILABLE)
        return await service.aggregate_social_data(
            case_id=request.case_id,
            group_by=request.group_by,
            platforms=request.platforms,
            query=request.query,
            date_from=request.date_from,
            date_to=request.date_to,
            limit=request.limit,
        )

    async def query_findings(arguments: BaseModel) -> dict[str, Any]:
        request = QueryFindingsInput.model_validate(arguments)
        if not _check(service, request.case_id):
            return dict(_UNAVAILABLE)
        return await service.query_findings(
            case_id=request.case_id,
            finding_id=request.finding_id,
            kind=request.kind,
            status=request.status,
            query=request.query,
            limit=request.limit,
            offset=request.offset,
        )

    async def query_review_items(arguments: BaseModel) -> dict[str, Any]:
        request = QueryReviewItemsInput.model_validate(arguments)
        if not _check(service, request.case_id):
            return dict(_UNAVAILABLE)
        return await service.query_review_items(
            case_id=request.case_id,
            review_item_id=request.review_item_id,
            object_type=request.object_type,
            object_id=request.object_id,
            status=request.status,
            limit=request.limit,
            offset=request.offset,
        )

    async def query_reports(arguments: BaseModel) -> dict[str, Any]:
        request = QueryReportsInput.model_validate(arguments)
        if not _check(service, request.case_id):
            return dict(_UNAVAILABLE)
        return await service.query_reports(
            case_id=request.case_id,
            report_id=request.report_id,
            status=request.status,
            include_content_preview=request.include_content_preview,
            limit=request.limit,
            offset=request.offset,
        )

    async def query_case_activity(arguments: BaseModel) -> dict[str, Any]:
        request = QueryCaseActivityInput.model_validate(arguments)
        if not _check(service, request.case_id):
            return dict(_UNAVAILABLE)
        return await service.query_case_activity(
            case_id=request.case_id,
            activity_type=request.activity_type,
            actor=request.actor,
            limit=request.limit,
            offset=request.offset,
        )

    _REGISTRATIONS: list[ToolSpec] = [
        ToolSpec(
            name="get_case_data_overview",
            description=(
                "Use this tool for authoritative current persisted counts, "
                "per-platform totals, case-level data coverage, and active "
                "collection status. Use it when the user asks \"how many records "
                "are in the database now\", \"how many posts were collected\", or "
                "\"what is the current persisted case state\". Do not infer exact "
                "counts from conversation history, memory, or semantic search."
            ),
            input_model=CaseDataOverviewInput,
            handler=get_case_data_overview,
            output_model=CaseDataOverviewOutput,
            **_DB_TOOL_CONFIG,
        ),
        ToolSpec(
            name="query_social_posts",
            description=(
                "Query the current case's persisted Source Posts using "
                "deterministic database filters such as platform, lexical text "
                "match, author, date range, and sort order. Use it for exact "
                "record lists, latest posts, and platform-specific data. The "
                "query parameter is lexical substring matching, not semantic "
                "retrieval. Use search_social_evidence instead for semantic "
                "evidence discovery."
            ),
            input_model=QuerySocialPostsInput,
            handler=query_social_posts,
            output_model=QuerySocialPostsOutput,
            **_DB_TOOL_CONFIG,
        ),
        ToolSpec(
            name="get_social_post",
            description=(
                "Fetch one exact persisted Source Post by stable post_id or "
                "platform + native_id. Use when the user refers to a specific "
                "post or when another tool returns a Post ID that must be "
                "inspected precisely. This tool does not validate whether claims "
                "inside the post are true."
            ),
            input_model=GetSocialPostInput,
            handler=get_social_post,
            output_model=GetSocialPostOutput,
            **_DB_TOOL_CONFIG,
        ),
        ToolSpec(
            name="query_social_comments",
            description=(
                "Query persisted comments for the current case using exact "
                "database filters. Use for comment lists, comment text, or "
                "comments attached to a known Post. Do not use it as a semantic "
                "evidence search engine."
            ),
            input_model=QuerySocialCommentsInput,
            handler=query_social_comments,
            output_model=QuerySocialCommentsOutput,
            **_DB_TOOL_CONFIG,
        ),
        ToolSpec(
            name="aggregate_social_data",
            description=(
                "Compute exact deterministic post-count aggregations over "
                "current persisted social data, grouped by platform, day, or "
                "content type. Use for questions such as \"how many posts are on "
                "each platform\". Do not estimate counts from sampled search "
                "results."
            ),
            input_model=AggregateSocialDataInput,
            handler=aggregate_social_data,
            output_model=AggregateSocialDataOutput,
            **_DB_TOOL_CONFIG,
        ),
        ToolSpec(
            name="query_findings",
            description=(
                "Query persisted Findings and their current workflow status. Use "
                "for questions about candidate / under_review / verified / "
                "rejected findings. Only verified findings represent "
                "Human-Review-accepted conclusions."
            ),
            input_model=QueryFindingsInput,
            handler=query_findings,
            output_model=QueryFindingsOutput,
            **_DB_TOOL_CONFIG,
        ),
        ToolSpec(
            name="query_review_items",
            description=(
                "Query the current Human Review state for case-scoped objects "
                "such as Findings. Use when the user asks whether an object has "
                "been reviewed, approved, rejected, or what review "
                "version/status it is currently in. This tool is read-only."
            ),
            input_model=QueryReviewItemsInput,
            handler=query_review_items,
            output_model=QueryReviewItemsOutput,
            **_DB_TOOL_CONFIG,
        ),
        ToolSpec(
            name="query_reports",
            description=(
                "Query ReportDocument records and their current status for the "
                "case. Use for exact report lists, publication status, or report "
                "identity. Do not use this tool to regenerate or modify reports."
            ),
            input_model=QueryReportsInput,
            handler=query_reports,
            output_model=QueryReportsOutput,
            **_DB_TOOL_CONFIG,
        ),
        ToolSpec(
            name="query_case_activity",
            description=(
                "Query the current case activity log using deterministic filters "
                "such as activity_type and actor. Use when the user asks what "
                "operations recently occurred in the case. This tool exposes only "
                "a bounded safe activity summary."
            ),
            input_model=QueryCaseActivityInput,
            handler=query_case_activity,
            output_model=QueryCaseActivityOutput,
            **_DB_TOOL_CONFIG,
        ),
    ]

    for spec in _REGISTRATIONS:
        if spec.name not in registry.names():
            registry.register(spec)
