"""Durable agent loop graph.

Every model call and every tool batch is a checkpointed LangGraph node, so
a worker crash between nodes can resume from the last checkpoint instead of
replaying the whole run. In-flight approvals use LangGraph ``interrupt``:
the graph pauses inside the tool node, the worker persists an ``approvals``
row and flips the run to ``waiting_approval``, and a later resume continues
from the exact interrupted tool call.

The graph is instantiated per run; domain logic stays inside
:class:`AgentRuntime` and the Tool Registry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.harness.hooks import HookBus
from app.harness.runtime import (
    AgentDefinition,
    AgentRuntime,
    RuntimeContext,
    RuntimeEventSink,
)
from app.infrastructure.llm import LLMGateway, LLMMessage, ToolCall


class AgentLoopState(TypedDict, total=False):
    messages: list[dict[str, Any]]
    turn: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost: float
    tool_call_count: int
    model: str
    content: str
    status: str
    pending_tool_calls: list[dict[str, Any]]


class AgentLoopGraph:
    """A checkpointed model/tool loop for one agent run."""

    def __init__(
        self,
        *,
        gateway: LLMGateway,
        tools: Any,
        hooks: HookBus,
        event_sink: RuntimeEventSink,
        definition: AgentDefinition,
        context: RuntimeContext,
        checkpointer: Any = None,
        steering_loader: Callable[[], Awaitable[list[str]]] | None = None,
        cancel_event: asyncio.Event | None = None,
        authorization: Any = None,
    ) -> None:
        self._runtime = AgentRuntime(
            gateway,
            tools,
            hooks,
            event_sink=event_sink,
            approval_handler=self._approval,
            authorization=authorization,
        )
        self._definition = definition
        self._context = context
        self._steering_loader = steering_loader
        self._cancel_event = cancel_event
        self._graph = self._build().compile(checkpointer=checkpointer)

    async def _approval(self, request: dict[str, Any]) -> dict[str, Any]:
        """Pause the graph until the worker resumes with a user decision."""
        return interrupt(request)

    def _build(self) -> StateGraph[AgentLoopState]:
        graph = StateGraph(AgentLoopState)
        graph.add_node("steering_step", self._steering_step)
        graph.add_node("model_step", self._model_step)
        graph.add_node("tool_step", self._tool_step)
        graph.add_node("finish", self._finish)
        # Steering is folded in before every model step (start and after each
        # tool batch) so mid-run instructions reach the next model call.
        graph.add_edge(START, "steering_step")
        graph.add_edge("steering_step", "model_step")
        graph.add_conditional_edges(
            "model_step",
            self._route,
            {"tool_step": "tool_step", "finish": "finish"},
        )
        graph.add_edge("tool_step", "steering_step")
        graph.add_edge("finish", END)
        return graph

    async def _steering_step(self, state: AgentLoopState) -> dict[str, Any]:
        if self._steering_loader is None:
            return {}
        steerings = await self._steering_loader()
        if not steerings:
            return {}
        messages = [LLMMessage.model_validate(m) for m in state["messages"]]
        for content in steerings:
            messages.append(LLMMessage(role="user", content=content))
        return {"messages": [m.model_dump() for m in messages]}

    def _route(self, state: AgentLoopState) -> str:
        return "tool_step" if state.get("pending_tool_calls") else "finish"

    async def _model_step(self, state: AgentLoopState) -> dict[str, Any]:
        messages = [LLMMessage.model_validate(m) for m in state["messages"]]
        response = await self._runtime.step_model(
            messages=messages,
            definition=self._definition,
            context=self._context,
            turn_index=state.get("turn", 0),
            model_call_id=str(uuid4()),
            current_cost=state.get("total_cost", 0.0),
        )
        return {
            "messages": [m.model_dump() for m in [*messages, response.message]],
            "turn": state.get("turn", 0) + 1,
            "total_input_tokens": (
                state.get("total_input_tokens", 0) + response.usage.input_tokens
            ),
            "total_output_tokens": (
                state.get("total_output_tokens", 0) + response.usage.output_tokens
            ),
            "total_cost": state.get("total_cost", 0.0) + response.estimated_cost,
            "model": response.model,
            "pending_tool_calls": [c.model_dump() for c in response.tool_calls],
        }

    async def _tool_step(self, state: AgentLoopState) -> dict[str, Any]:
        calls = [
            ToolCall.model_validate(c) for c in state.get("pending_tool_calls", [])
        ]
        results = await self._runtime.step_tools(
            calls,
            definition=self._definition,
            context=self._context,
            cancel_event=self._cancel_event,
        )
        messages = [LLMMessage.model_validate(m) for m in state["messages"]]
        messages.extend(results)
        return {
            "messages": [m.model_dump() for m in messages],
            "tool_call_count": state.get("tool_call_count", 0) + len(calls),
            "pending_tool_calls": [],
        }

    async def _finish(self, state: AgentLoopState) -> dict[str, Any]:
        content = ""
        for message in reversed(state["messages"]):
            if message.get("role") == "assistant":
                content = message.get("content") or ""
                break
        return {"status": "completed", "content": content}

    async def ainvoke(
        self,
        state: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._graph.ainvoke(state, config)

    async def aget_state(self, config: dict[str, Any]) -> Any:
        return await self._graph.aget_state(config)
