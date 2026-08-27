"""M10: full agent-loop stability — budgets, cancellation, redaction.

These tests drive :class:`AgentRuntime` with a scripted Fake LLM to prove
the loop's guardrails: multi-turn tool cycling, every budget exhaustion
code, tool errors surfaced as results (never crashes), cancellation
propagation, and sensitive-key redaction in audit events.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.errors import ApplicationError
from app.harness.hooks import HookBus
from app.harness.runtime import (
    AgentDefinition,
    AgentRuntime,
    RuntimeContext,
    _redact,
    _summarize,
)
from app.harness.tools import ToolRegistry, ToolSpec
from app.infrastructure.llm import (
    LLMGateway,
    LLMMessage,
    LLMResponse,
    ModelRoute,
    ToolCall,
)

ScriptStep = Callable[[list[LLMMessage]], LLMResponse]


def tool_response(
    name: str,
    arguments: dict[str, Any],
    *,
    call_id: str,
    cost: float = 0.0,
) -> LLMResponse:
    return LLMResponse(
        message=LLMMessage(
            role="assistant",
            tool_calls=[
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        ),
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        estimated_cost=cost,
        model="fake-model",
        priced=True,
    )


def text_response(
    content: str,
    *,
    cost: float = 0.0,
) -> LLMResponse:
    return LLMResponse(
        message=LLMMessage(role="assistant", content=content),
        estimated_cost=cost,
        model="fake-model",
        priced=True,
    )


class FakeGateway(LLMGateway):
    """Scripted gateway; each step either a fixed response or a callback."""

    def __init__(
        self,
        *,
        script: list[LLMResponse | ScriptStep],
        configured: bool = True,
    ) -> None:
        self.calls = 0
        self.script = script
        self._configured = configured
        self.history: list[list[LLMMessage]] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        route: ModelRoute,
        temperature: float = 0,
    ) -> LLMResponse:
        self.calls += 1
        assert route is ModelRoute.FAST
        self.history.append(list(messages))
        if self.calls <= len(self.script):
            step = self.script[self.calls - 1]
            if callable(step):
                return step(messages)
            return step
        return text_response("done")


class EchoInput(BaseModel):
    text: str


class EchoHandler:
    def __init__(self) -> None:
        self.invocations: list[dict[str, Any]] = []
        self.failure: str | None = None
        self.block: asyncio.Event | None = None

    async def __call__(self, arguments: BaseModel) -> dict[str, Any]:
        request = EchoInput.model_validate(arguments)
        self.invocations.append(dict(request))
        if self.failure:
            raise ApplicationError(self.failure, code="tool_broken")
        if self.block is not None:
            await self.block.wait()
        return {"text": request.text, "ok": True}


def build_runtime(
    gateway: FakeGateway,
    *,
    handler: EchoHandler | None = None,
    approval_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    requires_approval: bool = False,
    permissions: tuple[str, ...] = ("read_evidence",),
) -> tuple[AgentRuntime, list[dict[str, Any]]]:
    tools = ToolRegistry()
    echo = handler or EchoHandler()
    tools.register(
        ToolSpec(
            name="echo",
            version="1.0.0",
            description="Return supplied evidence.",
            input_model=EchoInput,
            handler=echo,
            permissions=permissions,
            requires_approval=requires_approval,
        )
    )
    events: list[dict[str, Any]] = []

    async def capture(event: dict[str, Any]) -> None:
        events.append(event)

    runtime = AgentRuntime(
        gateway,
        tools,
        HookBus(),
        event_sink=capture,
        approval_handler=approval_handler,
    )
    return runtime, events


def definition(**overrides: Any) -> AgentDefinition:
    base: dict[str, Any] = {
        "name": "researcher",
        "instructions": "Use echo and answer.",
        "model_route": ModelRoute.FAST,
        "allowed_tools": frozenset({"echo"}),
        "permissions": frozenset({"read_evidence"}),
    }
    base.update(overrides)
    return AgentDefinition(**base)


async def run_loop(
    runtime: AgentRuntime,
    agent: AgentDefinition,
) -> Any:
    return await runtime.run(
        agent,
        user_message="分析证据",
        system_context="case=test",
        context=RuntimeContext(run_id="run-1", case_id="case-1", turn_id="turn-1"),
    )


async def test_multi_turn_tool_cycle_then_final_answer() -> None:
    gateway = FakeGateway(
        script=[
            tool_response("echo", {"text": "第一轮"}, call_id="c1"),
            tool_response("echo", {"text": "第二轮"}, call_id="c2"),
            text_response("最终结论"),
        ]
    )
    handler = EchoHandler()
    runtime, events = build_runtime(gateway, handler=handler)

    result = await run_loop(runtime, definition())

    assert result.content == "最终结论"
    assert result.tool_calls == 2
    assert handler.invocations == [{"text": "第一轮"}, {"text": "第二轮"}]
    # Each tool result is fed back to the model as a tool message.
    assert gateway.history[1][-1].role == "tool"
    assert gateway.history[1][-1].tool_call_id == "c1"
    assert gateway.history[2][-1].role == "tool"
    assert gateway.history[2][-1].tool_call_id == "c2"
    kinds = [event["event_type"] for event in events]
    assert kinds.count("model_call_start") == 3
    assert kinds.count("tool_execution_end") == 2
    assert all(
        event["status"] == "completed"
        for event in events
        if event["event_type"] == "tool_execution_end"
    )


async def test_long_run_converges_with_complete_event_stream() -> None:
    # Ten tool turns then a final answer: the loop keeps working the whole
    # way and emits one complete start/end pair per turn (no drift).
    script: list[LLMResponse | ScriptStep] = [
        tool_response("echo", {"text": f"第{i}轮"}, call_id=f"c{i}") for i in range(10)
    ]
    script.append(text_response("长跑收敛"))
    gateway = FakeGateway(script=script)
    runtime, events = build_runtime(gateway)

    result = await run_loop(runtime, definition())

    assert result.content == "长跑收敛"
    assert result.tool_calls == 10
    starts = [e for e in events if e["event_type"] == "model_call_start"]
    ends = [e for e in events if e["event_type"] == "model_call_end"]
    tool_ends = [e for e in events if e["event_type"] == "tool_execution_end"]
    assert len(starts) == len(ends) == 11  # 10 tool turns + 1 final answer
    assert len(tool_ends) == 10
    assert {e["tool_call_id"] for e in tool_ends} == {f"c{i}" for i in range(10)}


async def test_max_turns_exhausted_raises_turn_budget() -> None:
    gateway = FakeGateway(
        script=[
            tool_response("echo", {"text": "x"}, call_id=f"c{i}")
            for i in range(8)
        ]
    )
    runtime, _ = build_runtime(gateway)

    with pytest.raises(ApplicationError) as exc_info:
        await run_loop(runtime, definition(max_turns=2, max_tool_calls=48))

    assert exc_info.value.code == "agent_turn_budget_exhausted"
    assert gateway.calls == 2  # both turns consumed before giving up


async def test_max_tool_calls_exhausted_raises_tool_budget() -> None:
    gateway = FakeGateway(
        script=[tool_response("echo", {"text": "x"}, call_id="c1")]
    )
    handler = EchoHandler()
    runtime, _ = build_runtime(gateway, handler=handler)

    # max_tool_calls=0: even a single batched call trips the budget.
    with pytest.raises(ApplicationError) as exc_info:
        await run_loop(runtime, definition(max_tool_calls=0))

    assert exc_info.value.code == "tool_budget_exhausted"
    assert gateway.calls == 1
    # The budget check happens before executing tools, so no tool ran.
    assert handler.invocations == []


async def test_model_cost_budget_exhausted() -> None:
    gateway = FakeGateway(
        script=[
            # First turn must call a tool so the loop continues past the
            # immediate-return branch.
            tool_response("echo", {"text": "x"}, call_id="c1", cost=4.0),
            text_response("超预算的回答", cost=1.5),
        ]
    )
    runtime, _ = build_runtime(gateway)

    with pytest.raises(ApplicationError) as exc_info:
        await run_loop(runtime, definition(max_cost=5.0))

    assert exc_info.value.code == "cost_budget_exhausted"
    assert gateway.calls == 2


async def test_llm_not_configured_fails_fast() -> None:
    gateway = FakeGateway(script=[], configured=False)
    runtime, _ = build_runtime(gateway)

    with pytest.raises(ApplicationError) as exc_info:
        await run_loop(runtime, definition())

    assert exc_info.value.code == "llm_not_configured"
    assert gateway.calls == 0  # never even attempted a model call


async def test_tool_error_becomes_result_not_crash() -> None:
    gateway = FakeGateway(
        script=[
            tool_response("echo", {"text": "boom"}, call_id="c1"),
            # Second turn must see the failure surfaced as a tool message.
            lambda messages: (
                text_response("已感知失败并继续")
                if messages[-1].role == "tool" and '"ok": false' in (messages[-1].content or "")
                else tool_response("echo", {"text": "重试"}, call_id="c2")
            ),
        ]
    )
    handler = EchoHandler()
    handler.failure = "检索服务不可用"
    runtime, events = build_runtime(gateway, handler=handler)

    result = await run_loop(runtime, definition())

    assert result.content == "已感知失败并继续"
    failed = [
        event
        for event in events
        if event["event_type"] == "tool_execution_end" and event["status"] == "failed"
    ]
    assert len(failed) == 1
    assert failed[0]["error_code"] == "tool_broken"
    assert handler.invocations == [{"text": "boom"}]  # only one attempt


async def test_permission_denied_is_surfaced_to_model() -> None:
    gateway = FakeGateway(
        script=[
            tool_response("echo", {"text": "x"}, call_id="c1"),
            lambda messages: (
                text_response("权限不足已上报")
                if '"tool_permission_denied"' in (messages[-1].content or "")
                else text_response("unexpected")
            ),
        ]
    )
    runtime, events = build_runtime(gateway)

    # Agent has no permissions at all; tool requires read_evidence.
    result = await run_loop(runtime, definition(permissions=frozenset()))

    assert result.content == "权限不足已上报"
    failed = [
        event
        for event in events
        if event["event_type"] == "tool_execution_end" and event["status"] == "failed"
    ]
    assert failed[0]["error_code"] == "tool_permission_denied"


async def test_cancel_before_tool_start_reports_cancelled() -> None:
    gateway = FakeGateway(
        script=[
            tool_response("echo", {"text": "x"}, call_id="c1"),
            lambda messages: (
                text_response("取消已传播")
                if '"tool_cancelled"' in (messages[-1].content or "")
                else text_response("unexpected")
            ),
        ]
    )
    handler = EchoHandler()
    runtime, events = build_runtime(gateway, handler=handler)
    cancel = asyncio.Event()
    cancel.set()  # cancelled before the loop starts

    result = await runtime.run(
        definition(),
        user_message="go",
        system_context="",
        context=RuntimeContext(run_id="run-1", case_id="case-1", turn_id="turn-1"),
        cancel_event=cancel,
    )

    assert result.content == "取消已传播"
    assert handler.invocations == []  # handler never ran
    cancelled = [
        event
        for event in events
        if event["event_type"] == "tool_execution_end" and event["status"] == "cancelled"
    ]
    assert len(cancelled) == 1
    assert cancelled[0]["error_code"] == "tool_cancelled"
    assert cancelled[0]["duration_ms"] == 0


async def test_cancel_during_handler_stops_tool() -> None:
    gateway = FakeGateway(
        script=[
            tool_response("echo", {"text": "slow"}, call_id="c1"),
            lambda messages: (
                text_response("已放弃慢工具")
                if '"tool_cancelled"' in (messages[-1].content or "")
                else text_response("unexpected")
            ),
        ]
    )
    handler = EchoHandler()
    handler.block = asyncio.Event()
    runtime, events = build_runtime(gateway, handler=handler)
    cancel = asyncio.Event()

    async def cancel_after_short_delay() -> None:
        await asyncio.sleep(0.02)
        cancel.set()

    task = asyncio.create_task(cancel_after_short_delay())
    try:
        result = await runtime.run(
            definition(),
            user_message="go",
            system_context="",
            context=RuntimeContext(run_id="run-1", case_id="case-1", turn_id="turn-1"),
            cancel_event=cancel,
        )
    finally:
        task.cancel()

    assert result.content == "已放弃慢工具"
    cancelled = [
        event
        for event in events
        if event["event_type"] == "tool_execution_end" and event["status"] == "cancelled"
    ]
    assert len(cancelled) == 1


async def test_approval_rejection_returns_result_to_model() -> None:
    gateway = FakeGateway(
        script=[
            tool_response("echo", {"text": "costly"}, call_id="c1"),
            lambda messages: (
                text_response("用户拒绝了该调用")
                if '"tool_rejected_by_user"' in (messages[-1].content or "")
                else text_response("unexpected")
            ),
        ]
    )
    handler = EchoHandler()

    async def reject(request: dict[str, Any]) -> dict[str, Any]:
        assert request["action"] == "echo"
        return {"approved": False, "approval_id": "apr-1"}

    runtime, events = build_runtime(
        gateway,
        handler=handler,
        approval_handler=reject,
        requires_approval=True,
    )

    result = await run_loop(runtime, definition())

    assert result.content == "用户拒绝了该调用"
    assert handler.invocations == []  # handler never ran
    rejected = [
        event
        for event in events
        if event["event_type"] == "tool_execution_end" and event["status"] == "rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0]["approval_id"] == "apr-1"
    assert gateway.history[1][-1].tool_call_id == "c1"


async def test_approval_grant_then_executes() -> None:
    gateway = FakeGateway(
        script=[
            tool_response("echo", {"text": "costly"}, call_id="c1"),
            text_response("已执行"),
        ]
    )
    handler = EchoHandler()

    async def approve(request: dict[str, Any]) -> dict[str, Any]:
        return {"approved": True, "approval_id": "apr-2"}

    runtime, events = build_runtime(
        gateway,
        handler=handler,
        approval_handler=approve,
        requires_approval=True,
    )

    result = await run_loop(runtime, definition())

    assert result.content == "已执行"
    assert handler.invocations == [{"text": "costly"}]
    approval_events = [
        event for event in events if event["event_type"] == "approval_required"
    ]
    assert len(approval_events) == 1
    assert approval_events[0]["status"] == "waiting_approval"


async def test_edit_and_approve_executes_only_edited_arguments() -> None:
    gateway = FakeGateway(
        script=[
            tool_response("echo", {"text": "original"}, call_id="c1"),
            text_response("已按编辑参数执行"),
        ]
    )
    handler = EchoHandler()

    async def approve_with_edits(request: dict[str, Any]) -> dict[str, Any]:
        assert request["tool_call"]["arguments"] == {"text": "original"}
        return {
            "approved": True,
            "approval_id": "apr-edited",
            "edited_action": {
                "tool": "echo",
                "arguments": {"text": "edited"},
            },
        }

    runtime, _events = build_runtime(
        gateway,
        handler=handler,
        approval_handler=approve_with_edits,
        requires_approval=True,
    )

    result = await run_loop(runtime, definition())

    assert result.content == "已按编辑参数执行"
    assert handler.invocations == [{"text": "edited"}]
def test_redact_sensitive_keys_nested() -> None:
    payload = {
        "query": "普通文本",
        # Parent key "credentials" itself matches the marker and redacts
        # the whole subtree; use "auth" to exercise nested key matching.
        "auth": {
            "cookie": "session=abc",
            "api_key": "sk-123",
            "Authorization": "Bearer token",
        },
        "args": {"token": "t0k3n", "secret": "s3cr3t", "password": "p@ss"},
        "safe": {"nested": {"value": "keep"}},
    }
    redacted = _redact(payload)
    assert redacted["query"] == "普通文本"
    assert redacted["auth"]["cookie"] == "***"
    assert redacted["auth"]["api_key"] == "***"
    assert redacted["auth"]["Authorization"] == "***"  # case-insensitive
    assert redacted["args"]["token"] == "***"
    assert redacted["args"]["secret"] == "***"
    assert redacted["args"]["password"] == "***"
    assert redacted["safe"] == {"nested": {"value": "keep"}}

    # Marker-matched parent keys redact their entire subtree.
    assert _redact({"credentials": {"cookie": "abc", "note": "x"}}) == {
        "credentials": "***"
    }


def test_redact_covers_list_elements_and_scalars() -> None:
    assert _redact("cookie=abc", key="cookie") == "***"
    assert _redact([{"apiKey": "x"}]) == [{"apiKey": "***"}]
    assert _redact("plain", key="note") == "plain"


def test_summarize_truncates_and_redacts() -> None:
    long_payload = {"text": "x" * 600}
    summary = _summarize(long_payload)
    assert len(summary) == 501  # 500 chars + ellipsis
    assert summary.endswith("…")

    secret = _summarize({"cookie": "session=abc"})
    assert "session=abc" not in secret
    assert '"***"' in secret


def test_summarize_none_and_empty() -> None:
    assert _summarize(None) == ""
    assert _summarize({}) == ""
