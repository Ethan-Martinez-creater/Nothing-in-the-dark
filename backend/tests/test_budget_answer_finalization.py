"""A bounded synthesis cannot reopen the tool loop or hide an interrupted answer."""

import asyncio

import pytest
from pydantic import BaseModel

from app.core.errors import ApplicationError
from app.graphs.agent_loop import AgentLoopGraph
from app.harness.hooks import HookBus
from app.harness.runtime import AgentDefinition, AgentRuntime, RuntimeContext
from app.harness.tools import ToolRegistry, ToolSpec
from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse, ModelRoute, ToolCall
from app.infrastructure.llm.gateway import TokenUsage


class Empty(BaseModel):
    pass


class Gateway(LLMGateway):
    configured = True

    def __init__(self, final="answer", started=None):
        self.final = final
        self.requests = []
        self.started = started

    async def complete(self, *, messages, tools, route, temperature=0):
        self.requests.append((messages, tools))
        assert len(self.requests) <= 2
        calls = []
        content = None
        if len(self.requests) == 1:
            calls = [ToolCall(id=str(i), name="read", arguments={}) for i in range(2)]
        elif self.final == "error":
            raise ApplicationError("provider unavailable", code="model_provider_unavailable")
        elif self.final == "wait":
            if self.started:
                self.started.set()
            await asyncio.sleep(10)
        elif self.final == "tools":
            calls = [ToolCall(id="forged", name="write_case_memory", arguments={})]
        elif self.final != "empty":
            content = "本案样本观点不同，来源 source-1；仅为作者自述，未独立核验。"
        return LLMResponse(
            message=LLMMessage(role="assistant", content=content),
            tool_calls=calls,
            model="fake",
            usage=TokenUsage(input_tokens=2, output_tokens=3),
        )


def setup(final="answer", *, failing=False, started=None):
    tools = ToolRegistry()
    invocations = []

    async def read(_):
        invocations.append(1)
        if failing:
            raise ApplicationError("read unavailable", code="eval_injected_persistent_read")
        return {"posts": [{"id": "source-1", "content": "作者自述测试不一致"}]}

    tools.register(ToolSpec("read", "1", "read", Empty, read))
    definition = AgentDefinition("test", "test", ModelRoute.FAST, frozenset({"read"}))
    context = RuntimeContext(
        "run",
        "case",
        "turn",
        metadata={
            "execution_policy": {
                "read_only": True,
                "max_tool_calls": 1,
                "budget_scope": "run_tree",
            }
        },
    )
    events = []

    async def sink(event):
        events.append(event)

    return Gateway(final, started), tools, definition, context, events, sink, invocations


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["runtime", "graph"])
async def test_successful_read_gets_one_tool_free_synthesis_with_usage(engine):
    gateway, tools, definition, context, events, sink, invoked = setup()
    if engine == "runtime":
        result = await AgentRuntime(gateway, tools, HookBus(), event_sink=sink).run(
            definition,
            user_message="compare",
            system_context="",
            context=context,
        )
        content, count, input_tokens = result.content, result.tool_calls, result.input_tokens
    else:
        graph = AgentLoopGraph(
            gateway=gateway,
            tools=tools,
            hooks=HookBus(),
            event_sink=sink,
            definition=definition,
            context=context,
        )
        result = await graph.ainvoke(
            {
                "messages": [
                    LLMMessage(role="system", content="test").model_dump(),
                    LLMMessage(role="user", content="compare").model_dump(),
                ]
            },
            {},
        )
        content, count, input_tokens = (
            result["content"],
            result["tool_call_count"],
            result["total_input_tokens"],
        )
    assert "source-1" in content
    assert len(invoked) == count == 1
    assert len(gateway.requests) == 2
    assert gateway.requests[0][1] and gateway.requests[1][1] == []
    assert input_tokens == 4
    assert any("剩余1次" in (m.content or "") for m in gateway.requests[0][0])
    assert events[-1]["event_type"] == "answer_finalization_end"
    assert events[-1]["outcome"] == "succeeded"


@pytest.mark.asyncio
async def test_all_failed_reads_stop_without_extra_model_call():
    gateway, tools, definition, context, events, sink, invoked = setup(failing=True)
    result = await AgentRuntime(gateway, tools, HookBus(), event_sink=sink).run(
        definition,
        user_message="statistics",
        system_context="",
        context=context,
    )
    assert len(gateway.requests) == len(invoked) == result.tool_calls == 1
    assert "不提供未经核验的精确统计" in result.content
    assert "恢复后" in result.content
    assert not any(e["event_type"] == "answer_finalization_start" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "final,outcome",
    [
        ("error", "failed"),
        ("empty", "empty"),
        ("tools", "rejected_tool_calls"),
        ("wait", "timed_out"),
    ],
)
async def test_finalization_failure_is_bounded_and_never_executes_tools(
    final, outcome, monkeypatch
):
    monkeypatch.setattr("app.harness.runtime.ANSWER_FINALIZATION_TIMEOUT_SECONDS", 0.05)
    gateway, tools, definition, context, events, sink, invoked = setup(final)
    result = await AgentRuntime(gateway, tools, HookBus(), event_sink=sink).run(
        definition,
        user_message="compare",
        system_context="",
        context=context,
    )
    assert len(gateway.requests) == 2 and gateway.requests[-1][1] == []
    assert len(invoked) == result.tool_calls == 1
    assert result.content.startswith("本次运行已达到工具调用上限")
    assert [e for e in events if e["event_type"] == "answer_finalization_end"][-1][
        "outcome"
    ] == outcome
    if final == "tools":
        assert any(e.get("reason") == "answer_finalization_tools_disabled" for e in events)


@pytest.mark.asyncio
async def test_cancellation_during_finalization_propagates_without_fabricating_answer():
    started = asyncio.Event()
    cancel = asyncio.Event()
    gateway, tools, definition, context, events, sink, invoked = setup("wait", started=started)
    runtime = AgentRuntime(gateway, tools, HookBus(), event_sink=sink)
    task = asyncio.create_task(
        runtime.run(
            definition,
            user_message="compare",
            system_context="",
            context=context,
            cancel_event=cancel,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert len(invoked) == 1
    assert any(e.get("outcome") == "cancelled" for e in events)
