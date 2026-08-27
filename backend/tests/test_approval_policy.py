"""P0-1.4: crawl-scope expansion, budget and high-cost approval rules."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.core.errors import ApprovalRequiredError
from app.harness.approval_policy import (
    HIGH_COST_YUAN,
    budget_approval_needed,
    crawl_scope,
    crawl_scope_expanded,
    effective_max_cost,
    high_cost_tool,
)
from app.harness.hooks import HookBus
from app.harness.runtime import AgentDefinition, AgentRuntime, RuntimeContext
from app.harness.tools import ToolRegistry, ToolSpec
from app.infrastructure.llm import (
    LLMGateway,
    LLMMessage,
    LLMResponse,
    ModelRoute,
    ToolCall,
)


def test_adding_platform_is_scope_expansion() -> None:
    approved = crawl_scope({"platforms": ["weibo"], "time_range": {}})
    requested = crawl_scope({"platforms": ["weibo", "bilibili"], "time_range": {}})
    assert crawl_scope_expanded(approved, requested)
    assert not crawl_scope_expanded(requested, approved)


def test_wider_time_window_and_higher_limit_expand() -> None:
    approved = crawl_scope(
        {
            "platforms": ["weibo"],
            "time_range": {"start": "2026-08-01", "end": "2026-08-07"},
            "limit_per_platform": 10,
        }
    )
    wider = crawl_scope(
        {
            "platforms": ["weibo"],
            "time_range": {"start": "2026-07-01", "end": "2026-08-07"},
            "limit_per_platform": 10,
        }
    )
    more = crawl_scope(
        {
            "platforms": ["weibo"],
            "time_range": {"start": "2026-08-01", "end": "2026-08-07"},
            "limit_per_platform": 50,
        }
    )
    same = crawl_scope(
        {
            "platforms": ["weibo"],
            "time_range": {"start": "2026-08-01", "end": "2026-08-07"},
            "limit_per_platform": 10,
        }
    )
    assert crawl_scope_expanded(approved, wider)
    assert crawl_scope_expanded(approved, more)
    assert not crawl_scope_expanded(approved, same)


def test_budget_approval_when_already_at_cap() -> None:
    assert budget_approval_needed(5.0, max_cost=5.0, already_approved=False)
    assert not budget_approval_needed(5.0, max_cost=5.0, already_approved=True)
    assert not budget_approval_needed(1.0, max_cost=5.0, already_approved=False)


def test_high_cost_tool_threshold() -> None:
    assert high_cost_tool(HIGH_COST_YUAN)
    assert not high_cost_tool(0.2)


def test_effective_max_cost_honours_override() -> None:
    assert effective_max_cost(5.0, {"max_cost_override": 12}) == 12
    assert effective_max_cost(5.0, {}) == 5.0


class _CrawlArgs(BaseModel):
    platforms: list[str]
    topic: str = "t"
    time_range: dict[str, Any] = {}
    case_id: str | None = None
    limit_per_platform: int = 150
    per_day_limit: int = 150
    comment_limit: int = 10


def test_crawl_scope_defaults_match_actual_crawler_limits() -> None:
    scope = crawl_scope({"platforms": ["weibo"], "time_range": {}})
    assert scope["limit"] == 150
    assert scope["per_day_limit"] == 150
    assert scope["comment_limit"] == 10
    assert scope["keyword_groups_max"] == 3
    assert scope["upstream_candidate_limit_per_keyword"] == 150
    assert scope["upstream_candidate_limit_per_platform"] == 450


def test_crawl_scope_discloses_bounded_multi_day_upstream_candidates() -> None:
    scope = crawl_scope(
        {
            "platforms": ["weibo"],
            "time_range": {"start": "2026-08-01", "end": "2026-08-10"},
            "limit_per_platform": 150,
            "per_day_limit": 150,
        }
    )
    assert scope["upstream_candidate_limit_per_keyword"] == 600
    assert scope["upstream_candidate_limit_per_platform"] == 1800


def test_larger_comment_or_daily_limit_expands_scope() -> None:
    approved = crawl_scope(
        {
            "platforms": ["weibo"],
            "per_day_limit": 50,
            "comment_limit": 5,
        }
    )
    assert crawl_scope_expanded(
        approved,
        crawl_scope(
            {
                "platforms": ["weibo"],
                "per_day_limit": 51,
                "comment_limit": 5,
            }
        ),
    )
    assert crawl_scope_expanded(
        approved,
        crawl_scope(
            {
                "platforms": ["weibo"],
                "per_day_limit": 50,
                "comment_limit": 6,
            }
        ),
    )


class _SilentGateway(LLMGateway):
    @property
    def configured(self) -> bool:
        return True

    async def complete(self, **kwargs: Any) -> LLMResponse:
        return LLMResponse(
            message=LLMMessage(role="assistant", content="ok"),
            model="fake",
        )


def _runtime(
    handler: Any,
    *,
    estimated_cost: float = 0,
    requires_approval: bool = True,
    approval_handler: Any = None,
) -> AgentRuntime:
    tools = ToolRegistry()
    tools.register(
        ToolSpec(
            name="collect_social_posts",
            version="1.0.0",
            description="crawl",
            input_model=_CrawlArgs,
            handler=handler,
            requires_approval=requires_approval,
            estimated_cost=estimated_cost,
        )
    )
    return AgentRuntime(
        _SilentGateway(),
        tools,
        HookBus(),
        approval_handler=approval_handler,
    )


def _definition() -> AgentDefinition:
    return AgentDefinition(
        name="coordinator",
        instructions="x",
        model_route=ModelRoute.FAST,
        allowed_tools=frozenset({"collect_social_posts"}),
    )


async def test_expanded_crawl_requires_approval_even_if_preapproved() -> None:
    ran: list[list[str]] = []

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        ran.append(_CrawlArgs.model_validate(arguments).platforms)
        return {"posts": []}

    runtime = _runtime(handler)
    context = RuntimeContext(
        run_id="r1",
        case_id="c1",
        turn_id="t1",
        approved_tools={"collect_social_posts"},
        metadata={
            "approved_crawl_scope": crawl_scope({"platforms": ["weibo"]}),
        },
    )
    with pytest.raises(ApprovalRequiredError) as exc:
        await runtime.step_tools(
            [
                ToolCall(
                    id="call-1",
                    name="collect_social_posts",
                    arguments={"platforms": ["weibo", "zhihu"], "topic": "t"},
                )
            ],
            definition=_definition(),
            context=context,
        )
    assert "扩大" in exc.value.reason
    assert ran == []


async def test_same_scope_preapproved_crawl_runs() -> None:
    ran: list[int] = []

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        ran.append(1)
        return {"posts": []}

    runtime = _runtime(handler)
    context = RuntimeContext(
        run_id="r1",
        case_id="c1",
        turn_id="t1",
        approved_tools={"collect_social_posts"},
        metadata={"approved_crawl_scope": crawl_scope({"platforms": ["weibo"]})},
    )
    await runtime.step_tools(
        [
            ToolCall(
                id="call-1",
                name="collect_social_posts",
                arguments={"platforms": ["weibo"], "topic": "t"},
            )
        ],
        definition=_definition(),
        context=context,
    )
    assert ran == [1]


async def test_high_cost_tool_requires_approval() -> None:
    async def handler(arguments: BaseModel) -> dict[str, Any]:
        return {"ok": True}

    runtime = _runtime(handler, estimated_cost=2.0, requires_approval=False)
    context = RuntimeContext(run_id="r1", case_id="c1", turn_id="t1")
    with pytest.raises(ApprovalRequiredError) as exc:
        await runtime.step_tools(
            [
                ToolCall(
                    id="call-1",
                    name="collect_social_posts",
                    arguments={"platforms": ["weibo"], "topic": "t"},
                )
            ],
            definition=_definition(),
            context=context,
        )
    assert "高成本" in exc.value.reason


async def test_budget_at_cap_requests_approval_when_handler_present() -> None:
    seen: list[str] = []

    async def approve(request: dict[str, Any]) -> dict[str, Any]:
        seen.append(request["action"])
        return {"approved": True}

    runtime = AgentRuntime(
        _SilentGateway(),
        ToolRegistry(),
        HookBus(),
        approval_handler=approve,
    )
    context = RuntimeContext(run_id="r1", case_id="c1", turn_id="t1")
    definition = AgentDefinition(
        name="coordinator",
        instructions="x",
        model_route=ModelRoute.FAST,
        allowed_tools=frozenset(),
        max_cost=5.0,
    )
    await runtime.step_model(
        messages=[LLMMessage(role="user", content="hi")],
        definition=definition,
        context=context,
        turn_index=0,
        model_call_id="m1",
        current_cost=5.0,
    )
    assert seen == ["budget_exceeded"]
    assert context.metadata["max_cost_override"] == 10.0


async def test_rejected_crawl_returns_error_without_running() -> None:
    ran: list[int] = []

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        ran.append(1)
        return {"posts": []}

    async def reject(request: dict[str, Any]) -> dict[str, Any]:
        return {"approved": False, "approval_id": "ap1"}

    runtime = _runtime(handler, approval_handler=reject)
    context = RuntimeContext(run_id="r1", case_id="c1", turn_id="t1")
    messages = await runtime.step_tools(
        [
            ToolCall(
                id="call-1",
                name="collect_social_posts",
                arguments={"platforms": ["weibo"], "topic": "t"},
            )
        ],
        definition=_definition(),
        context=context,
    )
    assert ran == []
    assert "tool_rejected_by_user" in messages[0].content
