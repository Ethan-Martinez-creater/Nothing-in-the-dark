from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, Literal

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.errors import ApplicationError
from app.infrastructure.llm.pricing import estimate_deepseek_cost
from app.services.resilience import RetryPolicy


class ModelRoute(StrEnum):
    FAST = "fast"
    REASONING = "reasoning"
    REPORT = "report"


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class TokenUsage(BaseModel):
    input_tokens: int = 0
    cached_input_tokens: int = 0
    uncached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    message: LLMMessage
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str
    finish_reason: str | None = None
    estimated_cost: float = 0
    currency: str = "CNY"
    pricing_model: str | None = None
    priced: bool = False


class LLMGateway(ABC):
    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        route: ModelRoute,
        temperature: float = 0,
    ) -> LLMResponse: ...


class OpenAICompatibleGateway(LLMGateway):
    def __init__(self, settings: Settings, telemetry: Any = None) -> None:
        self._settings = settings
        self._telemetry = telemetry
        self._semaphore = asyncio.Semaphore(settings.llm_max_concurrency)
        # M22: 有界重试策略（指数退避 + 抖动 + Retry-After 尊重 + 时间预算）。
        self._retry_policy = RetryPolicy(
            max_attempts=settings.llm_max_retries + 1,
            base_backoff_seconds=1.0,
            max_backoff_seconds=8.0,
            total_time_budget_seconds=settings.resilience_time_budget_seconds,
        )
        self._client: AsyncOpenAI | None = None
        if self.configured:
            self._client = AsyncOpenAI(
                api_key=settings.llm_api_key.get_secret_value(),
                base_url=settings.llm_base_url or None,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )

    @property
    def configured(self) -> bool:
        return bool(
            self._settings.llm_api_key.get_secret_value()
            and self._settings.llm_fast_model
        )

    def model_for(self, route: ModelRoute) -> str:
        # Project policy: every route (fast / reasoning / report) calls the
        # flash model only. Reasoning/report overrides are intentionally
        # ignored so no request can ever be routed to a different model.
        return self._settings.llm_fast_model

    async def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        route: ModelRoute,
        temperature: float = 0,
    ) -> LLMResponse:
        if self._client is None:
            raise ApplicationError(
                "LLM is not configured. Fill LLM_API_KEY and LLM_FAST_MODEL in "
                "Project/backend/.env before starting an agent run.",
                code="llm_not_configured",
            )

        model = self.model_for(route)
        payload_messages = [
            message.model_dump(exclude_none=True) for message in messages
        ]
        # M19: llm.call span + 指标（不改变业务行为）。
        telemetry = self._telemetry
        span = None
        started = 0.0
        if telemetry is not None:
            telemetry.metrics.increment("llm.calls")
            started = time.perf_counter()
            span = telemetry.tracer.start_span(
                "llm.call",
                attributes={
                    "provider": self._settings.llm_provider,
                    "model": model,
                    "route": str(route),
                },
            )
        last_error: Exception | None = None
        retry_after: float | None = None
        async with self._semaphore:
            for attempt in range(1, self._retry_policy.max_attempts + 1):
                try:
                    client: Any = self._client
                    request_kwargs: dict[str, Any] = {
                        "model": model,
                        "messages": payload_messages,
                        "temperature": temperature,
                    }
                    # 只有 tools 非空时才携带 tools/tool_choice：接口对
                    # "tools 缺失但 tool_choice=null" 的组合会返回 400
                    # （tools 类型错误）。无工具调用保持纯 chat 请求体。
                    if tools:
                        request_kwargs["tools"] = tools
                        request_kwargs["tool_choice"] = "auto"
                    response = await client.chat.completions.create(**request_kwargs)
                    converted = self._convert_response(response)
                    if telemetry is not None and span is not None:
                        telemetry.metrics.increment(
                            "llm.tokens_input", converted.usage.input_tokens
                        )
                        telemetry.metrics.increment(
                            "llm.tokens_output", converted.usage.output_tokens
                        )
                        telemetry.metrics.increment(
                            "llm.cost_cny", converted.estimated_cost
                        )
                        telemetry.metrics.observe(
                            "llm.latency_ms",
                            (time.perf_counter() - started) * 1000,
                        )
                        telemetry.tracer.end_span(span)
                    return converted
                except (APIConnectionError, APITimeoutError) as exc:
                    last_error = exc
                    if telemetry is not None:
                        telemetry.metrics.increment("llm.retries")
                except APIStatusError as exc:
                    if telemetry is not None:
                        telemetry.metrics.increment("llm.errors")
                    if exc.status_code not in {408, 409, 429, 500, 502, 503, 504}:
                        if telemetry is not None and span is not None:
                            telemetry.tracer.end_span(
                                span, status="error", error_code="llm_request_failed"
                            )
                        raise ApplicationError(
                            f"LLM request failed with HTTP {exc.status_code}",
                            code="llm_request_failed",
                        ) from exc
                    last_error = exc
                    # M22: 尊重 429 Retry-After（不存在时按指数退避）。
                    if exc.status_code == 429 and exc.headers is not None:
                        raw = exc.headers.get("retry-after")
                        if raw is not None:
                            try:
                                retry_after = float(raw)
                            except (TypeError, ValueError):
                                retry_after = None
                if attempt < self._retry_policy.max_attempts:
                    await asyncio.sleep(
                        self._retry_policy.next_backoff(attempt, retry_after=retry_after)
                    )

        if telemetry is not None and span is not None:
            telemetry.tracer.end_span(
                span, status="error", error_code="llm_request_failed"
            )
        raise ApplicationError(
            f"LLM request failed after retries: {type(last_error).__name__}",
            code="llm_request_failed",
        ) from last_error

    @staticmethod
    def _convert_response(response: Any) -> LLMResponse:
        choice = response.choices[0]
        message = choice.message
        calls: list[ToolCall] = []
        raw_calls: list[dict[str, Any]] = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                raise ApplicationError(
                    f"Model returned invalid JSON arguments for tool "
                    f"'{call.function.name}'",
                    code="invalid_tool_arguments",
                ) from exc
            if not isinstance(arguments, dict):
                raise ApplicationError(
                    f"Tool '{call.function.name}' arguments must be an object",
                    code="invalid_tool_arguments",
                )
            calls.append(
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=arguments,
                )
            )
            raw_calls.append(
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
            )

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        cached_input_tokens = (
            getattr(usage, "prompt_cache_hit_tokens", 0) or 0
        )
        if not cached_input_tokens:
            prompt_details = getattr(usage, "prompt_tokens_details", None)
            cached_input_tokens = (
                getattr(prompt_details, "cached_tokens", 0) or 0
            )
        explicit_miss_tokens = (
            getattr(usage, "prompt_cache_miss_tokens", None)
            if usage is not None
            else None
        )
        uncached_input_tokens = (
            int(explicit_miss_tokens)
            if explicit_miss_tokens is not None
            else max(0, input_tokens - cached_input_tokens)
        )
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        cost = estimate_deepseek_cost(
            model=response.model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
        )
        return LLMResponse(
            message=LLMMessage(
                role="assistant",
                content=message.content,
                tool_calls=raw_calls or None,
            ),
            tool_calls=calls,
            usage=TokenUsage(
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                uncached_input_tokens=uncached_input_tokens,
                output_tokens=output_tokens,
                total_tokens=getattr(usage, "total_tokens", 0) or 0,
            ),
            model=response.model,
            finish_reason=choice.finish_reason,
            estimated_cost=cost.amount,
            currency=cost.currency,
            pricing_model=cost.pricing_model,
            priced=cost.priced,
        )
