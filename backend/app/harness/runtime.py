from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.core.errors import ApplicationError, ApprovalRequiredError
from app.harness.approval_policy import (
    budget_approval_needed,
    crawl_scope,
    crawl_scope_expanded,
    effective_max_cost,
    high_cost_tool,
)
from app.harness.hooks import HookBus, HookEvent
from app.harness.tools import ToolRegistry
from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse, ModelRoute, ToolCall

RuntimeEventSink = Callable[[dict[str, Any]], Awaitable[None]]
ApprovalHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_SENSITIVE_KEYS = frozenset(
    {
        "cookie",
        "cookies",
        "token",
        "api_key",
        "apikey",
        "secret",
        "password",
        "authorization",
        "credential",
    }
)
_SUMMARY_LIMIT = 500


def _redact(value: object, *, key: str = "") -> object:
    """Replace sensitive values and truncate long payloads for audit logs."""
    lowered = key.lower()
    if any(marker in lowered for marker in _SENSITIVE_KEYS):
        return "***"
    if isinstance(value, dict):
        return {k: _redact(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key=key) for v in value]
    return value


def _summarize(value: dict[str, object] | None) -> str:
    if not value:
        return ""
    redacted = json.dumps(_redact(value), ensure_ascii=False, default=str)
    if len(redacted) > _SUMMARY_LIMIT:
        return redacted[:_SUMMARY_LIMIT] + "…"
    return redacted


def _rag_metrics(spec: object, output: dict[str, Any]) -> dict[str, object] | None:
    """Structured hit summary for RAG tools, or None for other tools.

    Reads the ``hits`` list convention shared by retrieval tools; each hit
    may carry ``retrieval_modes`` (e.g. vector / keyword / hybrid).
    """
    if not getattr(spec, "rag_output", False):
        return None
    hits = output.get("hits") if isinstance(output, dict) else None
    if not isinstance(hits, list):
        return {"available": False, "hit_count": 0, "retrieval_modes": []}
    modes: set[str] = set()
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        for mode in hit.get("retrieval_modes") or []:
            if mode:
                modes.add(str(mode))
    return {
        "available": True,
        "hit_count": len(hits),
        "retrieval_modes": sorted(modes),
    }


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    name: str
    instructions: str
    model_route: ModelRoute
    allowed_tools: frozenset[str]
    permissions: frozenset[str] = frozenset()
    max_turns: int = 16
    max_tool_calls: int = 48
    max_cost: float = 5


@dataclass(slots=True)
class AgentRunResult:
    content: str
    messages: list[LLMMessage]
    input_tokens: int
    output_tokens: int
    tool_calls: int
    model: str
    estimated_cost: float
    currency: str


@dataclass(slots=True)
class RuntimeContext:
    run_id: str
    case_id: str
    turn_id: str
    approved_tools: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)
    # M21/M22: 当前恢复的审批 id（approved 后 resume 时注入），用于执行前
    # 原子消费一次性授权，防止同一审批重复放行副作用工具。
    approval_id: str | None = None


async def _discard_event(_: dict[str, Any]) -> None:
    return None


class AgentRuntime:
    """A model-driven loop shared by every domain agent."""

    def __init__(
        self,
        gateway: LLMGateway,
        tools: ToolRegistry,
        hooks: HookBus,
        *,
        event_sink: RuntimeEventSink = _discard_event,
        approval_handler: ApprovalHandler | None = None,
        authorization: Any = None,
    ) -> None:
        self._gateway = gateway
        self._tools = tools
        self._hooks = hooks
        self._event_sink = event_sink
        self._approval_handler = approval_handler
        # M21/M22: 一次性授权消费服务（可选注入；未注入时跳过消费，仅
        # 保留策略校验，供测试与兼容路径使用）。
        self._authorization = authorization

    async def run(
        self,
        definition: AgentDefinition,
        *,
        user_message: str,
        system_context: str,
        context: RuntimeContext,
        history: list[LLMMessage] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AgentRunResult:
        if not self._gateway.configured:
            raise ApplicationError(
                "The agent runtime requires an LLM configuration",
                code="llm_not_configured",
            )

        messages = list(history or [])
        messages.insert(
            0,
            LLMMessage(
                role="system",
                content=f"{definition.instructions}\n\n{system_context}".strip(),
            ),
        )
        user_payload = await self._hooks.emit(
            HookEvent.BEFORE_USER_MESSAGE,
            {"content": user_message, "context": context},
        )
        messages.append(LLMMessage(role="user", content=str(user_payload["content"])))
        await self._hooks.emit(HookEvent.AFTER_USER_MESSAGE, user_payload)

        total_input = 0
        total_output = 0
        total_tools = 0
        total_cost = 0.0
        last_model = ""

        try:
            for turn_index in range(definition.max_turns):
                model_call_id = str(uuid4())
                response = await self.step_model(
                    messages=messages,
                    definition=definition,
                    context=context,
                    turn_index=turn_index,
                    model_call_id=model_call_id,
                    current_cost=total_cost,
                )
                last_model = response.model
                total_input += response.usage.input_tokens
                total_output += response.usage.output_tokens
                total_cost += response.estimated_cost
                messages.append(response.message)

                if not response.tool_calls:
                    content = response.message.content or ""
                    await self._hooks.emit(
                        HookEvent.ON_AGENT_STOP,
                        {
                            "agent": definition.name,
                            "content": content,
                            "context": context,
                        },
                    )
                    return AgentRunResult(
                        content=content,
                        messages=messages,
                        input_tokens=total_input,
                        output_tokens=total_output,
                        tool_calls=total_tools,
                        model=last_model,
                        estimated_cost=total_cost,
                        currency="CNY",
                    )

                if total_tools + len(response.tool_calls) > definition.max_tool_calls:
                    raise ApplicationError(
                        "Agent tool-call budget exhausted",
                        code="tool_budget_exhausted",
                    )
                results = await self.step_tools(
                    response.tool_calls,
                    definition=definition,
                    context=context,
                    cancel_event=cancel_event,
                )
                total_tools += len(results)
                messages.extend(results)

            raise ApplicationError(
                "Agent reached its maximum turn budget",
                code="agent_turn_budget_exhausted",
            )
        except Exception as exc:
            await self._hooks.emit(
                HookEvent.ON_ERROR,
                {
                    "agent": definition.name,
                    "error": exc,
                    "context": context,
                },
            )
            raise

    async def step_model(
        self,
        *,
        messages: list[LLMMessage],
        definition: AgentDefinition,
        context: RuntimeContext,
        turn_index: int,
        model_call_id: str,
        current_cost: float,
    ) -> LLMResponse:
        """A single model call with hooks, events and budget enforcement."""
        llm_tools = self._tools.llm_tools(set(definition.allowed_tools))
        started_at = time.perf_counter()
        await self._hooks.emit(
            HookEvent.BEFORE_MODEL_CALL,
            {
                "agent": definition.name,
                "turn_index": turn_index,
                "context": context,
            },
        )
        await self._event_sink(
            {
                "event_type": "model_call_start",
                "agent": definition.name,
                "run_id": context.run_id,
                "turn_id": context.turn_id,
                "model_call_id": model_call_id,
                "route": definition.model_route,
            }
        )
        max_cost = effective_max_cost(definition.max_cost, context.metadata)
        if budget_approval_needed(
            current_cost,
            max_cost=max_cost,
            already_approved="budget_exceeded" in context.approved_tools,
        ):
            await self._request_approval(
                action="budget_exceeded",
                reason=(
                    f"当前费用 ¥{current_cost:.2f} 已达预算上限 ¥{max_cost:.2f}，"
                    "继续调用模型需要批准。"
                ),
                request_payload={
                    "approval_kind": "budget_exceeded",
                    "current_cost": current_cost,
                    "max_cost": max_cost,
                },
                definition=definition,
                context=context,
                tool_call=None,
            )
            context.approved_tools.add("budget_exceeded")
            context.metadata["max_cost_override"] = max_cost + definition.max_cost
            max_cost = float(context.metadata["max_cost_override"])
        response = await self._gateway.complete(
            messages=messages,
            tools=llm_tools,
            route=definition.model_route,
        )
        if current_cost + response.estimated_cost > max_cost:
            raise ApplicationError(
                "Agent model cost budget exhausted",
                code="cost_budget_exhausted",
            )
        await self._hooks.emit(
            HookEvent.AFTER_MODEL_CALL,
            {
                "agent": definition.name,
                "response": response,
                "context": context,
            },
        )
        await self._event_sink(
            {
                "event_type": "model_call_end",
                "agent": definition.name,
                "run_id": context.run_id,
                "turn_id": context.turn_id,
                "model_call_id": model_call_id,
                "model": response.model,
                "route": definition.model_route,
                "usage": response.usage.model_dump(),
                "estimated_cost": response.estimated_cost,
                "currency": response.currency,
                "pricing_model": response.pricing_model,
                "priced": response.priced,
                "latency_ms": int((time.perf_counter() - started_at) * 1000),
            }
        )
        return response

    async def step_tools(
        self,
        calls: list[ToolCall],
        *,
        definition: AgentDefinition,
        context: RuntimeContext,
        cancel_event: asyncio.Event | None = None,
    ) -> list[LLMMessage]:
        """Execute a batch of tool calls (sequential when required)."""
        sequential = any(
            self._tools.get(call.name).execution_mode == "sequential"
            for call in calls
        )
        if sequential:
            return [
                await self._execute_tool(
                    call,
                    definition=definition,
                    context=context,
                    cancel_event=cancel_event,
                )
                for call in calls
            ]
        return list(
            await asyncio.gather(
                *(
                    self._execute_tool(
                        call,
                        definition=definition,
                        context=context,
                        cancel_event=cancel_event,
                    )
                    for call in calls
                )
            )
        )

    async def _execute_tool(
        self,
        call: ToolCall,
        *,
        definition: AgentDefinition,
        context: RuntimeContext,
        cancel_event: asyncio.Event | None = None,
    ) -> LLMMessage:
        if call.name not in definition.allowed_tools:
            result: dict[str, Any] = {
                "ok": False,
                "error": {
                    "code": "tool_not_allowed",
                    "message": f"Agent '{definition.name}' cannot use '{call.name}'",
                },
            }
            return self._tool_message(call, result)
        spec = self._tools.get(call.name)
        arguments = dict(call.arguments)
        if call.name in {
            "collect_social_posts",
            "search_social_evidence",
            "write_case_memory",
            "dispatch_expert",
            "get_artifact",
            "reconstruct_propagation",
            "verify_claims",
            "query_claims",
            "query_evidence",
            "query_propagation",
        }:
            # Case scope is controlled by the runtime, never by model output.
            arguments["case_id"] = context.case_id
        if call.name == "verify_claims":
            # The creator run is also runtime controlled so persisted claims
            # always reference the real run that produced them.
            arguments["run_id"] = context.run_id
        if call.name == "dispatch_expert":
            # The parent run and a stable idempotency key are also runtime
            # controlled: the call id is stable across checkpoint replays.
            arguments["run_id"] = context.run_id
            arguments["dispatch_key"] = f"{context.run_id}:{call.id}"

        is_memory_write = call.name == "write_case_memory"
        if is_memory_write:
            await self._hooks.emit(
                HookEvent.BEFORE_MEMORY_WRITE,
                {"agent": definition.name, "tool_call": call, "context": context},
            )
        await self._hooks.emit(
            HookEvent.BEFORE_TOOL_CALL,
            {"agent": definition.name, "tool_call": call, "context": context},
        )
        await self._event_sink(
            {
                "event_type": "tool_execution_start",
                "agent": definition.name,
                "run_id": context.run_id,
                "turn_id": context.turn_id,
                "tool_call_id": call.id,
                "tool": call.name,
                "skill_name": None,
                "estimated_cost": spec.estimated_cost,
                "idempotent": spec.idempotent,
            }
        )

        if cancel_event is not None and cancel_event.is_set():
            # The run was cancelled before this call started: report it as
            # cancelled without invoking the handler.
            cancelled_result = {
                "ok": False,
                "error": {
                    "code": "tool_cancelled",
                    "message": f"Tool call '{call.name}' was cancelled",
                },
            }
            await self._hooks.emit(
                HookEvent.AFTER_TOOL_CALL,
                {
                    "agent": definition.name,
                    "tool_call": call,
                    "result": cancelled_result,
                    "context": context,
                },
            )
            await self._event_sink(
                {
                    "event_type": "tool_execution_end",
                    "agent": definition.name,
                    "run_id": context.run_id,
                    "turn_id": context.turn_id,
                    "tool_call_id": call.id,
                    "tool": call.name,
                    "status": "cancelled",
                    "error_code": "tool_cancelled",
                    "duration_ms": 0,
                    "retry_count": 0,
                    "retry_history": [],
                    "cached": False,
                    "estimated_cost": 0.0,
                    "arguments": _redact(arguments),
                    "input_summary": _summarize(arguments),
                    "output_summary": "",
                    "idempotency_key": f"{context.run_id}:{call.id}",
                }
            )
            return self._tool_message(call, cancelled_result)

        # In-flight approval: pause instead of failing when the user has not
        # pre-approved a risky or costly tool. The caller decides how to
        # continue; without a handler the run is interrupted.
        approved = call.name in context.approved_tools
        requested_scope = (
            crawl_scope(arguments) if call.name == "collect_social_posts" else None
        )
        prior_scope = context.metadata.get("approved_crawl_scope")
        scope_expanded = bool(
            requested_scope
            and isinstance(prior_scope, dict)
            and crawl_scope_expanded(prior_scope, requested_scope)
        )
        needs_approval = (
            (spec.requires_approval and not approved)
            or (high_cost_tool(spec.estimated_cost) and not approved)
            or scope_expanded
        )
        if needs_approval:
            kind = "collect"
            reason = (
                f"Tool '{call.name}' requires user approval "
                f"(estimated cost ¥{spec.estimated_cost}, "
                f"side effect: {spec.side_effect})"
            )
            if scope_expanded:
                kind = "crawl_scope_expand"
                reason = (
                    "采集范围已扩大（平台、时间窗或条数上限），需再次审批。"
                )
            elif high_cost_tool(spec.estimated_cost) and not spec.requires_approval:
                kind = "high_cost_tool"
                reason = (
                    f"工具 '{call.name}' 预估费用 ¥{spec.estimated_cost}，"
                    "属于高成本调用，需批准后执行。"
                )
            request: dict[str, Any] = {
                "action": call.name,
                "reason": reason,
                "request_payload": {
                    "tool": call.name,
                    "approval_kind": kind,
                    "arguments_summary": _summarize(arguments),
                    "estimated_cost": spec.estimated_cost,
                    "side_effect": spec.side_effect,
                    "crawl_scope": requested_scope,
                },
                "tool_call": {
                    "id": call.id,
                    "name": call.name,
                    "arguments": _redact(arguments),
                },
            }
            await self._event_sink(
                {
                    "event_type": "approval_required",
                    "agent": definition.name,
                    "run_id": context.run_id,
                    "turn_id": context.turn_id,
                    "tool_call_id": call.id,
                    "tool": call.name,
                    "status": "waiting_approval",
                    "action": request["action"],
                    "reason": request["reason"],
                    "request_payload": request["request_payload"],
                }
            )
            if self._approval_handler is None:
                raise ApprovalRequiredError(
                    request["reason"],
                    action=request["action"],
                    reason=request["reason"],
                    request_payload=request["request_payload"],
                )
            decision = await self._approval_handler(request)
            if not decision.get("approved", False):
                result = {
                    "ok": False,
                    "error": {
                        "code": "tool_rejected_by_user",
                        "message": f"User rejected tool call '{call.name}'",
                        "approval_id": decision.get("approval_id"),
                    },
                }
                await self._hooks.emit(
                    HookEvent.AFTER_TOOL_CALL,
                    {
                        "agent": definition.name,
                        "tool_call": call,
                        "result": result,
                        "context": context,
                    },
                )
                await self._event_sink(
                    {
                        "event_type": "tool_execution_end",
                        "agent": definition.name,
                        "run_id": context.run_id,
                        "turn_id": context.turn_id,
                        "tool_call_id": call.id,
                        "tool": call.name,
                        "status": "rejected",
                        "approval_id": decision.get("approval_id"),
                        "duration_ms": 0,
                        "retry_count": 0,
                        "retry_history": [],
                        "cached": False,
                        "estimated_cost": 0.0,
                        "arguments": _redact(arguments),
                        "input_summary": _summarize(arguments),
                        "output_summary": "",
                    }
                )
                return self._tool_message(call, result)
            edited_action = decision.get("edited_action")
            if edited_action is not None:
                if not isinstance(edited_action, dict):
                    raise ApplicationError(
                        "Invalid edited approval payload",
                        code="approval_edit_arguments_invalid",
                    )
                edited_tool = str(edited_action.get("tool") or call.name)
                edited_arguments = edited_action.get("arguments")
                if edited_tool != call.name:
                    raise ApplicationError(
                        "Edited approval cannot change the tool",
                        code="approval_edit_tool_changed",
                    )
                if not isinstance(edited_arguments, dict):
                    raise ApplicationError(
                        "Edited approval arguments must be an object",
                        code="approval_edit_arguments_invalid",
                    )
                arguments = dict(edited_arguments)
                requested_scope = (
                    crawl_scope(arguments)
                    if call.name == "collect_social_posts"
                    else None
                )
            if requested_scope:
                context.metadata["approved_crawl_scope"] = requested_scope
                context.approved_tools.add(call.name)

        if requested_scope:
            context.metadata["approved_crawl_scope"] = requested_scope

        # M21/M22: 已审批工具的恢复执行必须原子消费一次性授权（防重放）。
        # 预批准路径（approve_crawl=True，无审批记录）或未装配授权服务时
        # 跳过；有 approval_id 的工具执行只能成功消费一次。
        if (
            context.approval_id is not None
            and self._authorization is not None
            and (spec.requires_approval or call.name in context.approved_tools)
        ):
            try:
                await self._authorization.consume_for_tool(
                    context.approval_id,
                    run_id=context.run_id,
                    tool_name=call.name,
                    arguments=dict(arguments),
                )
            except ApplicationError as exc:
                raise ApplicationError(
                    f"Approval '{context.approval_id}' cannot authorize "
                    f"'{call.name}': {exc}",
                    code="authorization_not_consumed",
                ) from exc

        started_at = time.perf_counter()
        retry_history: list[dict[str, object]] = []

        def on_retry(entry: dict[str, object]) -> None:
            retry_history.append(entry)

        try:
            invocation = await self._tools.invoke_with_meta(
                call.name,
                arguments,
                granted_permissions=set(definition.permissions),
                approved=True,
                on_retry=on_retry,
                cancel_event=cancel_event,
                security_context={
                    "run_id": context.run_id,
                    "turn_id": context.turn_id,
                    "tool_call_id": call.id,
                },
            )
            output = invocation.output
            cached = invocation.cached
            result = {"ok": True, "data": output, "cached": cached}
        except ApplicationError as exc:
            result = {
                "ok": False,
                "error": {"code": exc.code, "message": str(exc)},
            }
        duration_ms = int(
            (time.perf_counter() - started_at) * 1000
        )
        await self._hooks.emit(
            HookEvent.AFTER_TOOL_CALL,
            {
                "agent": definition.name,
                "tool_call": call,
                "result": result,
                "context": context,
            },
        )
        if is_memory_write:
            await self._hooks.emit(
                HookEvent.AFTER_MEMORY_WRITE,
                {
                    "agent": definition.name,
                    "tool_call": call,
                    "result": result,
                    "context": context,
                },
            )
        cached = bool(result.get("cached"))
        await self._event_sink(
            {
                "event_type": "tool_execution_end",
                "agent": definition.name,
                "run_id": context.run_id,
                "turn_id": context.turn_id,
                "tool_call_id": call.id,
                "tool": call.name,
                "status": (
                    "cancelled"
                    if not result["ok"]
                    and result["error"]["code"] == "tool_cancelled"
                    else ("completed" if result["ok"] else "failed")
                ),
                "error_code": (
                    result["error"]["code"]
                    if not result["ok"]
                    else None
                ),
                "duration_ms": duration_ms,
                "retry_count": len(retry_history),
                "retry_history": retry_history,
                "cached": cached,
                # A cache hit reuses a previous result and costs nothing.
                "estimated_cost": (
                    spec.estimated_cost if result["ok"] and not cached else 0.0
                ),
                "arguments": _redact(arguments),
                "input_summary": _summarize(arguments),
                "output_summary": _summarize(
                    {"data": output} if result["ok"] else None
                ),
                "rag": (
                    _rag_metrics(spec, output) if result["ok"] else None
                ),
                "idempotency_key": f"{context.run_id}:{call.id}",
            }
        )
        return self._tool_message(call, result)

    async def _request_approval(
        self,
        *,
        action: str,
        reason: str,
        request_payload: dict[str, Any],
        definition: AgentDefinition,
        context: RuntimeContext,
        tool_call: dict[str, Any] | None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "action": action,
            "reason": reason,
            "request_payload": request_payload,
        }
        if tool_call is not None:
            request["tool_call"] = tool_call
        await self._event_sink(
            {
                "event_type": "approval_required",
                "agent": definition.name,
                "run_id": context.run_id,
                "turn_id": context.turn_id,
                "tool": action,
                "status": "waiting_approval",
                "action": action,
                "reason": reason,
                "request_payload": request_payload,
            }
        )
        if self._approval_handler is None:
            raise ApprovalRequiredError(
                reason,
                action=action,
                reason=reason,
                request_payload=request_payload,
            )
        decision = await self._approval_handler(request)
        if not decision.get("approved", False):
            raise ApplicationError(
                "User rejected additional budget",
                code="cost_budget_exhausted",
            )
        return decision

    @staticmethod
    def _tool_message(call: ToolCall, result: dict[str, Any]) -> LLMMessage:
        return LLMMessage(
            role="tool",
            tool_call_id=call.id,
            name=call.name,
            content=json.dumps(result, ensure_ascii=False, default=str),
        )
