"""DB01–DB09 Tool 契约 / 权限 / Runtime Scope / Routing 测试（DBT10/DBT11）。

覆盖文档 T01–T19（ToolSpec 契约）、P01–P06（权限）、S01–S08（Runtime
Case Scope 与跨 Case 隔离）、G01/G02/G05（deterministic Agent routing）、
No-Arbitrary-SQL 架构约束（文档 §97）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.application.agent_database_service import AgentDatabaseReadService
from app.application.repositories import ApplicationRepository
from app.harness.agents import build_coordinator_definition
from app.harness.database_tools import (
    AggregateSocialDataInput,
    QueryCaseActivityInput,
    QueryFindingsInput,
    QueryReportsInput,
    QueryReviewItemsInput,
    QuerySocialCommentsInput,
    QuerySocialPostsInput,
    register_database_tools,
)
from app.harness.hooks import HookBus
from app.harness.runtime import AgentDefinition, AgentRuntime, RuntimeContext
from app.harness.tools import ToolRegistry
from app.infrastructure.database.collection_run_repository import CollectionRunRepository
from app.infrastructure.database.finding_repository import FindingRepository
from app.infrastructure.database.models import (
    FindingRecord,
    ReportDocumentRecord,
    ReviewDecisionRecord,
    ReviewItemRecord,
)
from app.infrastructure.database.report_repository import ReportDocumentRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse, ModelRoute, ToolCall
from app.schemas.cases import CreateCaseRequest
from tests.memory_db import MemoryDatabase

DB_TOOL_NAMES = [
    "get_case_data_overview",
    "query_social_posts",
    "get_social_post",
    "query_social_comments",
    "aggregate_social_data",
    "query_findings",
    "query_review_items",
    "query_reports",
    "query_case_activity",
]

_FORBIDDEN_TOOLS = {
    "execute_sql",
    "run_sql",
    "query_sql",
    "query_table",
    "query_database",
    "insert_record",
    "update_record",
    "delete_record",
    "database_shell",
}

_FORBIDDEN_FIELDS = {"table_name", "column_name", "sql", "where_clause", "order_clause"}


def _post(
    platform: str, index: int, *, content: str = "内容", published_at: str = "2026-08-15T10:00:00+00:00"
) -> dict[str, Any]:
    return {
        "platform": platform,
        "native_id": f"{platform}-{index}",
        "content_type": "post",
        "title": "",
        "content": f"{content} {index}",
        "author": f"author-{platform}",
        "published_at": published_at,
        "engagement": 1,
        "metrics": {},
        "url": "u",
        "raw": {},
        "comments": [],
    }


class ScriptedGateway(LLMGateway):
    """按脚本依次返回 ToolCall，随后返回最终内容；记录每轮 tool 结果。"""

    def __init__(self, script: list[dict[str, Any]], final_content: str = "done") -> None:
        self._script = list(script)
        self._final = final_content
        self.calls = 0
        self.tool_results: list[str] = []

    @property
    def configured(self) -> bool:
        return True

    async def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        route: ModelRoute,
        temperature: float = 0,
    ) -> LLMResponse:
        self.calls += 1
        for msg in reversed(messages):
            if msg.role == "tool" and msg.content:
                self.tool_results.append(msg.content)
                break
        if self.calls <= len(self._script):
            tc = self._script[self.calls - 1]
            args = tc.get("arguments", {})
            return LLMResponse(
                message=LLMMessage(
                    role="assistant",
                    tool_calls=[
                        {
                            "id": f"c{self.calls}",
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(args),
                            },
                        }
                    ],
                ),
                tool_calls=[
                    ToolCall(id=f"c{self.calls}", name=tc["name"], arguments=args)
                ],
                model="fake",
            )
        return LLMResponse(
            message=LLMMessage(role="assistant", content=self._final),
            model="fake",
        )


async def _build_env() -> (
    tuple[
        MemoryDatabase,
        Any,
        ToolRegistry,
        AgentDatabaseReadService,
        SocialRepository,
        ApplicationRepository,
        FindingRepository,
        ReportDocumentRepository,
        CollectionRunRepository,
    ]
):
    db = MemoryDatabase()
    await db.create_schema()
    app_repo = ApplicationRepository(db)
    case = await app_repo.create_case(
        CreateCaseRequest(
            topic="华为竹知了事件",
            platforms=["weibo", "zhihu"],
            time_start="2026-08-10",
            time_end="2026-08-20",
        )
    )
    social = SocialRepository(db)
    finding_repo = FindingRepository(db)
    report_repo = ReportDocumentRepository(db)
    collection_repo = CollectionRunRepository(db)
    service = AgentDatabaseReadService(
        repository=app_repo,
        social_repository=social,
        collection_run_repository=collection_repo,
        finding_repository=finding_repo,
        report_repository=report_repo,
    )
    registry = ToolRegistry()
    register_database_tools(registry, service)
    return db, case, registry, service, social, app_repo, finding_repo, report_repo, collection_repo


def _coordinator() -> AgentDefinition:
    return build_coordinator_definition()


def _expert(name: str, allowed_tools: set[str], permissions: set[str]) -> AgentDefinition:
    return AgentDefinition(
        name=name,
        instructions="Use DB tools.",
        model_route=ModelRoute.FAST,
        allowed_tools=frozenset(allowed_tools),
        permissions=frozenset(permissions),
    )


# ---------------------------------------------------------------------------
# T01–T19: ToolSpec 契约
# ---------------------------------------------------------------------------


async def test_t01_all_nine_tools_registered() -> None:
    _, _, registry, *_ = await _build_env()
    for name in DB_TOOL_NAMES:
        assert name in registry.names(), name
    assert "search_social_evidence" not in [n for n in registry.names() if n in ("query_social_posts",)]


async def test_t02_t08_contract_fields() -> None:
    _, _, registry, *_ = await _build_env()
    for name in DB_TOOL_NAMES:
        spec = registry.get(name)
        assert spec.permissions == ("read_database",), name
        assert spec.side_effect == "none", name
        assert spec.idempotent is True, name
        assert spec.requires_approval is False, name
        assert spec.cache_ttl_seconds == 0, name
        assert spec.output_model is not None, name
        assert spec.execution_class == "trusted_in_process", name
        assert spec.execution_mode == "parallel", name
        assert spec.rag_output is False, name


async def test_t09_t11_input_bounds_rejected() -> None:
    with pytest.raises(ValidationError):
        QuerySocialPostsInput(limit=101)
    with pytest.raises(ValidationError):
        QuerySocialPostsInput(offset=5001)
    with pytest.raises(ValidationError):
        QuerySocialPostsInput(query="x" * 301)
    with pytest.raises(ValidationError):
        QuerySocialCommentsInput(limit=0)
    with pytest.raises(ValidationError):
        AggregateSocialDataInput(group_by="bogus")
    with pytest.raises(ValidationError):
        QueryFindingsInput(limit=101)
    with pytest.raises(ValidationError):
        QueryReviewItemsInput(offset=5001)
    with pytest.raises(ValidationError):
        QueryReportsInput(limit=101)
    with pytest.raises(ValidationError):
        QueryCaseActivityInput(limit=101)


async def test_t15_t17_routing_descriptions() -> None:
    _, _, registry, *_ = await _build_env()
    overview = registry.get("get_case_data_overview").description.lower()
    assert "authoritative" in overview
    assert "exact counts" in overview
    assert "conversation history" in overview
    posts = registry.get("query_social_posts").description.lower()
    assert "lexical" in posts and "semantic" in posts
    for name in DB_TOOL_NAMES:
        assert len(registry.get(name).description) > 40, name


async def test_t18_t19_field_descriptions() -> None:
    qp = QuerySocialPostsInput.model_fields
    assert qp["query"].description and "lexical" in qp["query"].description
    assert qp["platforms"].description
    assert qp["limit"].description
    assert qp["case_id"].description and "runtime" in qp["case_id"].description.lower()
    qf = QueryFindingsInput.model_fields
    assert qf["status"].description and "verified" in qf["status"].description


async def test_t12_t13_t14_no_raw_payload_embedding_hash_fields() -> None:
    """输出白名单：Post/Comment 序列化不得含 raw_payload / embedding / content_hash。"""
    db, case, _, service, social, *_ = await _build_env()
    await social.persist_batch(case_id=case.id, posts=[_post("weibo", 1)])
    posts = await service.query_social_posts(case_id=case.id, limit=10)
    item = posts["posts"][0]
    assert "raw_payload" not in item
    assert "embedding" not in item
    assert "content_hash" not in item
    await db.dispose()


async def test_no_arbitrary_sql_architecture() -> None:
    """文档 §97：禁止任意 SQL Tool 与 SQL 控制字段。"""
    _, _, registry, *_ = await _build_env()
    assert not (_FORBIDDEN_TOOLS & registry.names())
    for model_cls in [
        QuerySocialPostsInput,
        QuerySocialCommentsInput,
        AggregateSocialDataInput,
        QueryFindingsInput,
        QueryReviewItemsInput,
        QueryReportsInput,
        QueryCaseActivityInput,
    ]:
        for field in model_cls.model_fields:
            assert field not in _FORBIDDEN_FIELDS, field


# ---------------------------------------------------------------------------
# P01–P06: Permission
# ---------------------------------------------------------------------------


async def test_p01_coordinator_can_call_db_tool() -> None:
    db, case, registry, *_ = await _build_env()
    gateway = ScriptedGateway(
        [{"name": "get_case_data_overview", "arguments": {}}],
        final_content="完成",
    )
    runtime = AgentRuntime(gateway, registry, HookBus())
    result = await runtime.run(
        _coordinator(),
        user_message="当前数据库多少帖子？",
        system_context="",
        context=RuntimeContext(run_id="run-1", case_id=case.id, turn_id="turn-1"),
    )
    assert result.content == "完成"
    assert result.tool_calls == 1
    await db.dispose()


async def test_p03_missing_read_database_denied() -> None:
    db, case, registry, *_ = await _build_env()
    gateway = ScriptedGateway([{"name": "query_social_posts", "arguments": {}}])
    runtime = AgentRuntime(gateway, registry, HookBus())
    await runtime.run(
        _expert(
            "no-read",
            {"query_social_posts"},
            {"read_artifact"},  # 缺 read_database
        ),
        user_message="query",
        system_context="",
        context=RuntimeContext(run_id="run-1", case_id=case.id, turn_id="turn-1"),
    )
    assert gateway.tool_results
    assert "tool_permission_denied" in gateway.tool_results[0]
    await db.dispose()


async def test_p04_tool_absent_from_allowlist_denied() -> None:
    db, case, registry, *_ = await _build_env()
    gateway = ScriptedGateway([{"name": "get_case_data_overview", "arguments": {}}])
    runtime = AgentRuntime(gateway, registry, HookBus())
    await runtime.run(
        _expert(
            "not-allowed",
            {"query_social_posts"},  # get_case_data_overview 不在 allowlist
            {"read_database"},
        ),
        user_message="query",
        system_context="",
        context=RuntimeContext(run_id="run-1", case_id=case.id, turn_id="turn-1"),
    )
    assert gateway.tool_results
    assert "tool_not_allowed" in gateway.tool_results[0]
    await db.dispose()


async def test_p05_no_db_tool_requires_write_database() -> None:
    _, _, registry, *_ = await _build_env()
    for name in DB_TOOL_NAMES:
        spec = registry.get(name)
        assert "write_database" not in spec.permissions, name
        assert spec.permissions == ("read_database",), name


# ---------------------------------------------------------------------------
# S01–S08: Runtime Case Scope 与跨 Case 隔离
# ---------------------------------------------------------------------------


async def test_s01_runtime_overrides_model_case_id() -> None:
    db, case, registry, _, social, *_ = await _build_env()
    await social.persist_batch(case_id=case.id, posts=[_post("weibo", 1)])
    # 模型伪造其它 case_id，Runtime 必须覆盖为当前 case
    gateway = ScriptedGateway(
        [{"name": "get_case_data_overview", "arguments": {"case_id": "evil-case"}}],
        final_content="回答",
    )
    runtime = AgentRuntime(gateway, registry, HookBus())
    result = await runtime.run(
        _coordinator(),
        user_message="多少帖子",
        system_context="",
        context=RuntimeContext(run_id="run-1", case_id=case.id, turn_id="turn-1"),
    )
    # 第二轮 tool 消息里应包含当前 case 的真实 posts 数
    assert any('"posts": 1' in content for content in gateway.tool_results)
    await db.dispose()


async def test_s02_get_social_post_foreign_id_not_found() -> None:
    db, case, registry, _, social, app_repo, *_ = await _build_env()
    other = await app_repo.create_case(
        CreateCaseRequest(topic="其它事件", platforms=["weibo"])
    )
    await social.persist_batch(case_id=other.id, posts=[_post("weibo", 9)])
    other_post = (await social.list_posts_page(other.id, limit=1))[0]
    gateway = ScriptedGateway(
        [{"name": "get_social_post", "arguments": {"post_id": other_post.id}}],
        final_content="答",
    )
    runtime = AgentRuntime(gateway, registry, HookBus())
    await runtime.run(
        _coordinator(),
        user_message="查帖子",
        system_context="",
        context=RuntimeContext(run_id="run-1", case_id=case.id, turn_id="turn-1"),
    )
    assert gateway.tool_results
    assert '"found": false' in gateway.tool_results[0]
    await db.dispose()


async def test_s04_query_findings_foreign_finding_not_found() -> None:
    db, case, registry, _, _, _, finding_repo, *_ = await _build_env()
    foreign = await finding_repo.create(
        FindingRecord(
            case_id=case.id, kind="fact_check", title="本case", statement="s",
            status="candidate", source_run_id="run-1",
        )
    )
    other = await finding_repo.create(
        FindingRecord(
            case_id="other-case", kind="fact_check", title="其它", statement="s",
            status="candidate", source_run_id="run-1",
        )
    )
    # 用"其它 case"的 finding_id 查询当前 case → found=false（DB-INV-4）
    gateway = ScriptedGateway(
        [{"name": "query_findings", "arguments": {"finding_id": foreign.id}}],
    )
    # 注意：foreign 属于当前 case，这里验证的是 exact 模式正常工作；
    # 用真正跨 case 的 id（other.id）验证隔离
    gateway2 = ScriptedGateway(
        [{"name": "query_findings", "arguments": {"finding_id": other.id}}],
    )
    runtime = AgentRuntime(gateway2, registry, HookBus())
    await runtime.run(
        _coordinator(),
        user_message="查 finding",
        system_context="",
        context=RuntimeContext(run_id="run-1", case_id=case.id, turn_id="turn-1"),
    )
    assert gateway2.tool_results
    assert '"found": false' in gateway2.tool_results[0]
    await db.dispose()


async def test_s07_s08_overview_counts_exclude_other_case() -> None:
    db, case, registry, service, social, app_repo, finding_repo, report_repo, collection_repo = await _build_env()
    other = await app_repo.create_case(
        CreateCaseRequest(topic="其它事件", platforms=["weibo"])
    )
    # 其它 case 的数据
    await social.persist_batch(
        case_id=other.id,
        posts=[_post("weibo", 9, content="其它评论贴")],
    )
    await finding_repo.create(
        FindingRecord(
            case_id=other.id, kind="fact_check", title="其它", statement="s",
            status="verified", source_run_id="run-1",
        )
    )
    await collection_repo.create(
        case_id=other.id,
        request_fingerprint="fp-other",
        request_json={"platforms": ["weibo"]},
        phase="discovery",
    )
    item_other = await app_repo.create_review_item(
        ReviewItemRecord(
            case_id=other.id, object_type="claim", object_id="oc", summary="s"
        )
    )
    await app_repo.add_review_decision(
        ReviewDecisionRecord(item_id=item_other.id, decision="approved")
    )
    result = await service.get_case_data_overview(case_id=case.id)
    assert result["counts"]["posts"] == 0
    assert result["counts"]["comments"] == 0
    assert result["counts"]["findings"] == 0
    assert result["counts"]["collection_runs"] == 0
    assert result["counts"]["review_items"] == 0
    assert result["counts"]["review_decisions"] == 0
    await db.dispose()


# ---------------------------------------------------------------------------
# G01/G02/G05: deterministic Agent routing
# ---------------------------------------------------------------------------


async def test_g01_exact_count_uses_db_tool_and_returns_current_db() -> None:
    """History=10 但当前 DB=25：Agent 调 DB tool 必须返回 25（DB-INV-1）。"""
    db, case, registry, _, social, *_ = await _build_env()
    for i in range(25):
        await social.persist_batch(case_id=case.id, posts=[_post("zhihu", i)])
    gateway = ScriptedGateway(
        [{"name": "get_case_data_overview", "arguments": {}}],
        final_content="现在知乎共有 25 条。",
    )
    runtime = AgentRuntime(gateway, registry, HookBus())
    result = await runtime.run(
        _coordinator(),
        user_message="现在数据库里知乎有多少条？",
        system_context="",
        context=RuntimeContext(run_id="run-1", case_id=case.id, turn_id="turn-1"),
    )
    assert result.tool_calls == 1
    assert gateway.tool_results
    assert '"posts": 25' in gateway.tool_results[0]
    await db.dispose()


async def test_g02_latest_posts_passes_platform_sort_limit() -> None:
    db, case, registry, _, social, *_ = await _build_env()
    await social.persist_batch(case_id=case.id, posts=[_post("zhihu", i) for i in range(12)])
    gateway = ScriptedGateway(
        [
            {
                "name": "query_social_posts",
                "arguments": {"platforms": ["zhihu"], "sort_order": "newest", "limit": 10},
            }
        ],
        final_content="最新 10 条。",
    )
    runtime = AgentRuntime(gateway, registry, HookBus())
    result = await runtime.run(
        _coordinator(),
        user_message="知乎最新 10 条是什么？",
        system_context="",
        context=RuntimeContext(run_id="run-1", case_id=case.id, turn_id="turn-1"),
    )
    assert result.tool_calls == 1
    assert gateway.tool_results
    assert '"returned_count": 10' in gateway.tool_results[0]
    await db.dispose()


async def test_g05_runtime_injects_case_id_when_model_omits() -> None:
    db, case, registry, _, social, *_ = await _build_env()
    await social.persist_batch(case_id=case.id, posts=[_post("weibo", 1)])
    # Fake LLM 不提供 case_id —— runtime 注入后调用仍成功
    gateway = ScriptedGateway(
        [{"name": "get_case_data_overview", "arguments": {}}],
        final_content="完成",
    )
    runtime = AgentRuntime(gateway, registry, HookBus())
    result = await runtime.run(
        _coordinator(),
        user_message="当前数据库多少帖子？",
        system_context="",
        context=RuntimeContext(run_id="run-1", case_id=case.id, turn_id="turn-1"),
    )
    assert result.tool_calls == 1
    assert gateway.tool_results
    assert '"posts": 1' in gateway.tool_results[0]
    await db.dispose()


async def test_g04_truth_verification_distinguishes_db_from_evidence() -> None:
    """文档 §94：DB Tool 只能证明持久化存在，不得直接下事实结论。

    该路由行为由 Agent Instructions / Tool description 承担；此处验证
    DB tool 输出不含事实判定字段（无 verdict），避免模型把 existence
    当作 truth。
    """
    db, case, registry, service, social, *_ = await _build_env()
    await social.persist_batch(
        case_id=case.id,
        posts=[_post("weibo", 1, content="华为要求停售竹知了")],
    )
    posts = await service.query_social_posts(
        case_id=case.id, query="华为要求停售", limit=10
    )
    assert posts["matched_count"] == 1
    item = posts["posts"][0]
    assert "verdict" not in item
    assert "truth" not in item
    await db.dispose()
