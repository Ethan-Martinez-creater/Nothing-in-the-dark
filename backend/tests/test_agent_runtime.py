from __future__ import annotations

from typing import Any

from pydantic import BaseModel

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


class EchoInput(BaseModel):
    text: str


class ScopedSearchInput(BaseModel):
    case_id: str
    query: str


class FakeGateway(LLMGateway):
    def __init__(self) -> None:
        self.calls = 0

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
        assert route is ModelRoute.FAST
        assert tools[0]["function"]["name"] == "echo"
        if self.calls == 1:
            return LLMResponse(
                message=LLMMessage(
                    role="assistant",
                    tool_calls=[
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "echo",
                                "arguments": '{"text":"evidence"}',
                            },
                        }
                    ],
                ),
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="echo",
                        arguments={"text": "evidence"},
                    )
                ],
                model="fake-model",
            )
        assert messages[-1].role == "tool"
        assert '"ok": true' in (messages[-1].content or "")
        return LLMResponse(
            message=LLMMessage(role="assistant", content="引用 evidence 完成回答"),
            model="fake-model",
        )


async def test_agent_runtime_executes_model_selected_tool() -> None:
    tools = ToolRegistry()

    async def echo(arguments: BaseModel) -> dict[str, Any]:
        request = EchoInput.model_validate(arguments)
        return {"text": request.text}

    tools.register(
        ToolSpec(
            name="echo",
            version="1.0.0",
            description="Return supplied evidence.",
            input_model=EchoInput,
            handler=echo,
            permissions=("read_evidence",),
        )
    )
    events: list[dict[str, Any]] = []

    async def capture(event: dict[str, Any]) -> None:
        events.append(event)

    runtime = AgentRuntime(FakeGateway(), tools, HookBus(), event_sink=capture)
    result = await runtime.run(
        AgentDefinition(
            name="researcher",
            instructions="Use evidence tools.",
            model_route=ModelRoute.FAST,
            allowed_tools=frozenset({"echo"}),
            permissions=frozenset({"read_evidence"}),
        ),
        user_message="分析证据",
        system_context="case=test",
        context=RuntimeContext(run_id="run-1", case_id="case-1", turn_id="turn-1"),
    )

    assert result.content == "引用 evidence 完成回答"
    assert result.tool_calls == 1
    assert [event["event_type"] for event in events] == [
        "model_call_start",
        "model_call_end",
        "tool_execution_start",
        "tool_execution_end",
        "model_call_start",
        "model_call_end",
    ]


async def test_runtime_overrides_model_supplied_case_scope() -> None:
    tools = ToolRegistry()
    observed_case_ids: list[str] = []

    async def search(arguments: BaseModel) -> dict[str, Any]:
        request = ScopedSearchInput.model_validate(arguments)
        observed_case_ids.append(request.case_id)
        return {"hits": []}

    tools.register(
        ToolSpec(
            name="search_social_evidence",
            version="1.0.0",
            description="Case-scoped evidence search.",
            input_model=ScopedSearchInput,
            handler=search,
            permissions=("read_database",),
        )
    )

    class ScopedGateway(LLMGateway):
        def __init__(self) -> None:
            self.calls = 0

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
            if self.calls == 1:
                return LLMResponse(
                    message=LLMMessage(role="assistant"),
                    tool_calls=[
                        ToolCall(
                            id="scoped-call",
                            name="search_social_evidence",
                            arguments={"case_id": "model-guessed-case", "query": "证据"},
                        )
                    ],
                    model="fake-model",
                )
            return LLMResponse(
                message=LLMMessage(role="assistant", content="done"),
                model="fake-model",
            )

    runtime = AgentRuntime(ScopedGateway(), tools, HookBus())
    await runtime.run(
        AgentDefinition(
            name="researcher",
            instructions="Search.",
            model_route=ModelRoute.FAST,
            allowed_tools=frozenset({"search_social_evidence"}),
            permissions=frozenset({"read_database"}),
        ),
        user_message="search",
        system_context="",
        context=RuntimeContext(
            run_id="run-1",
            case_id="runtime-owned-case",
            turn_id="turn-1",
        ),
    )

    assert observed_case_ids == ["runtime-owned-case"]
