"""M10: OpenAI-compatible gateway — concurrency cap, retries, validation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import APITimeoutError, RateLimitError

from app.core.config import Settings
from app.core.errors import ApplicationError
from app.infrastructure.llm import LLMMessage, ModelRoute, OpenAICompatibleGateway

SETTINGS = dict(
    llm_api_key="test-key",
    llm_fast_model="deepseek-v4-flash",
    database_url="sqlite+aiosqlite:///./data/test_llm_gateway.db",
)


def fake_response(*, text: str = "ok", tool_calls: list[Any] | None = None) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=tool_calls),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            prompt_tokens_details=None,
        ),
        model="deepseek-v4-flash",
    )


class FakeClient:
    def __init__(self, *, response: Any | None = None, error: Exception | None = None) -> None:
        self.response = response or fake_response()
        self.error = error
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.last_kwargs: dict[str, Any] = {}
        # Mirrors the SDK attribute chain used by the gateway.
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.error is not None:
                raise self.error
            await asyncio.sleep(0.02)
            self.last_kwargs = kwargs
            return self.response
        finally:
            self.active -= 1


def build_gateway(**overrides: Any) -> OpenAICompatibleGateway:
    settings = Settings(**SETTINGS, **overrides)
    gateway = OpenAICompatibleGateway(settings)
    assert gateway.configured
    return gateway


async def test_concurrency_capped_by_semaphore() -> None:
    gateway = build_gateway(llm_max_concurrency=2, llm_max_retries=0)
    client = FakeClient()
    gateway._client = client  # noqa: SLF001 — test seam for the SDK client

    async def one_call() -> None:
        await gateway.complete(
            messages=[LLMMessage(role="user", content="hi")],
            tools=[],
            route=ModelRoute.FAST,
        )

    await asyncio.gather(*[one_call() for _ in range(8)])

    assert client.calls == 8
    assert client.peak <= 2
    assert client.last_kwargs["model"] == "deepseek-v4-flash"
    assert client.last_kwargs["temperature"] == 0
    assert client.last_kwargs["tools"] is None  # no tools → None, not empty list


async def test_route_always_maps_to_flash_model() -> None:
    gateway = build_gateway(llm_max_retries=0)
    for route in (ModelRoute.FAST, ModelRoute.REASONING, ModelRoute.REPORT):
        assert gateway.model_for(route) == "deepseek-v4-flash"


async def test_invalid_tool_arguments_rejected() -> None:
    gateway = build_gateway(llm_max_retries=0)
    gateway._client = FakeClient(  # noqa: SLF001
        response=fake_response(
            tool_calls=[
                SimpleNamespace(
                    id="c1",
                    function=SimpleNamespace(name="echo", arguments="{broken"),
                )
            ]
        )
    )

    with pytest.raises(ApplicationError) as exc_info:
        await gateway.complete(
            messages=[LLMMessage(role="user", content="call")],
            tools=[{"type": "function", "function": {"name": "echo"}}],
            route=ModelRoute.FAST,
        )

    assert exc_info.value.code == "invalid_tool_arguments"


async def test_retries_then_reports_llm_request_failed() -> None:
    gateway = build_gateway(llm_max_retries=1)
    client = FakeClient(error=APITimeoutError("network"), response=None)
    gateway._client = client  # noqa: SLF001

    with pytest.raises(ApplicationError) as exc_info:
        await gateway.complete(
            messages=[LLMMessage(role="user", content="hi")],
            tools=[],
            route=ModelRoute.FAST,
        )

    assert exc_info.value.code == "llm_request_failed"
    assert client.calls == 2  # initial attempt + 1 retry


async def test_429_rate_limit_without_headers_attr_retries_and_succeeds() -> None:
    # openai>=2.x 的 RateLimitError 没有 `headers` 属性（retry-after 在
    # response.headers 上）。回归：此前 `exc.headers` 直接抛 AttributeError，
    # 掩盖真实限流导致重试失效。现在应安全读取 response.headers 并重试。
    class Flaky429Client:
        def __init__(self) -> None:
            self.calls = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs: Any) -> Any:
            self.calls += 1
            if self.calls == 1:
                request = httpx.Request("POST", "http://llm")
                response = httpx.Response(
                    429,
                    request=request,
                    headers={"retry-after": "0"},
                )
                raise RateLimitError(
                    "rate limited",
                    response=response,
                    body={"error": {"message": "too many requests"}},
                )
            return fake_response()

    gateway = build_gateway(llm_max_retries=2)
    gateway._client = Flaky429Client()  # noqa: SLF001

    result = await gateway.complete(
        messages=[LLMMessage(role="user", content="hi")],
        tools=[],
        route=ModelRoute.FAST,
    )
    assert result.message.content == "ok"
    assert gateway._client.calls == 2  # noqa: SLF001 — 1 次 429 + 1 次成功


def test_unconfigured_gateway_reports_llm_not_configured() -> None:
    settings = Settings(
        llm_api_key="",
        llm_fast_model="",
        database_url="sqlite+aiosqlite:///./data/test_llm_gateway.db",
    )
    gateway = OpenAICompatibleGateway(settings)
    assert not gateway.configured
