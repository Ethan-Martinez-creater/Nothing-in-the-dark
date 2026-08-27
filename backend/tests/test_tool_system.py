"""M6 Tool System: output schema validation, result caching, run-scoped
cancellation, concurrency policies, structured retry records and cost
accumulation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.application.ports.crawler import CrawlRequest
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.harness.hooks import HookBus
from app.harness.runtime import AgentDefinition, AgentRuntime, RuntimeContext
from app.harness.sandbox import SandboxedToolExecutor
from app.harness.tool_factory import build_tool_registry
from app.harness.tools import ToolRegistry, ToolSpec
from app.infrastructure.crawler.demo import DemoCrawlerAdapter
from app.infrastructure.llm import (
    LLMGateway,
    LLMMessage,
    LLMResponse,
    ModelRoute,
    ToolCall,
)
from app.main import create_app


class SampleInput(BaseModel):
    value: str


class SampleOutput(BaseModel):
    value: str
    count: int = 0

    model_config = {"extra": "allow"}


# ---------- 输出 Schema 校验 ----------


async def test_output_model_validated_on_success() -> None:
    registry = ToolRegistry()

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        request = SampleInput.model_validate(arguments)
        return {"value": request.value, "count": 3, "extra": "kept"}

    registry.register(
        ToolSpec(
            name="typed",
            version="1.0.0",
            description="Typed tool.",
            input_model=SampleInput,
            handler=handler,
            output_model=SampleOutput,
        )
    )
    invocation = await registry.invoke_with_meta("typed", {"value": "ok"})
    assert invocation.output == {"value": "ok", "count": 3, "extra": "kept"}


async def test_output_model_rejects_invalid_output() -> None:
    registry = ToolRegistry()

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        return {"count": "not-an-int"}  # missing required value, wrong count type

    registry.register(
        ToolSpec(
            name="broken",
            version="1.0.0",
            description="Broken typed tool.",
            input_model=SampleInput,
            handler=handler,
            output_model=SampleOutput,
        )
    )
    with pytest.raises(ApplicationError) as exc:
        await registry.invoke("broken", {"value": "x"})
    assert exc.value.code == "tool_output_invalid"


# ---------- 结果缓存 ----------


async def test_cache_hit_skips_handler_and_marks_cached() -> None:
    registry = ToolRegistry()
    calls = 0

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        request = SampleInput.model_validate(arguments)
        return {"value": request.value}

    registry.register(
        ToolSpec(
            name="cached_read",
            version="1.0.0",
            description="Read-only cached tool.",
            input_model=SampleInput,
            handler=handler,
            side_effect="none",
            idempotent=True,
            cache_ttl_seconds=60,
        )
    )
    first = await registry.invoke_with_meta("cached_read", {"value": "q"})
    second = await registry.invoke_with_meta("cached_read", {"value": "q"})
    third = await registry.invoke_with_meta("cached_read", {"value": "other"})

    assert calls == 2  # second call served from cache
    assert first.cached is False
    assert second.cached is True
    assert second.output == first.output
    assert third.cached is False


async def test_cache_only_for_idempotent_side_effect_free_tools() -> None:
    registry = ToolRegistry()
    calls = {"none": 0, "external": 0, "non_idempotent": 0}

    async def make_handler(key: str, *, side_effect: str, idempotent: bool):
        async def handler(arguments: BaseModel) -> dict[str, Any]:
            calls[key] += 1
            return {"value": key}

        return handler

    for key, side_effect, idempotent in [
        ("none", "none", True),
        ("external", "external_read", True),
        ("non_idempotent", "none", False),
    ]:
        registry.register(
            ToolSpec(
                name=f"tool_{key}",
                version="1.0.0",
                description=key,
                input_model=SampleInput,
                handler=await make_handler(key, side_effect=side_effect, idempotent=idempotent),
                side_effect=side_effect,
                idempotent=idempotent,
                cache_ttl_seconds=60,
            )
        )

    for name in ("tool_none", "tool_external", "tool_non_idempotent"):
        await registry.invoke(name, {"value": "x"})
        await registry.invoke(name, {"value": "x"})

    assert calls == {"none": 1, "external": 2, "non_idempotent": 2}


async def test_cache_expires_after_ttl() -> None:
    registry = ToolRegistry()
    calls = 0

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"value": "x"}

    registry.register(
        ToolSpec(
            name="ttl_read",
            version="1.0.0",
            description="Short TTL.",
            input_model=SampleInput,
            handler=handler,
            cache_ttl_seconds=1,
        )
    )
    await registry.invoke("ttl_read", {"value": "x"})
    assert calls == 1
    await asyncio.sleep(1.1)
    await registry.invoke("ttl_read", {"value": "x"})
    assert calls == 2


# ---------- 运行中取消传播 ----------


async def test_cancel_propagates_into_running_handler() -> None:
    registry = ToolRegistry()
    released = asyncio.Event()
    handler_started = asyncio.Event()
    finally_ran = False

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        nonlocal finally_ran
        handler_started.set()
        try:
            await released.wait()
        finally:
            finally_ran = True
        return {"value": "never"}

    registry.register(
        ToolSpec(
            name="slow",
            version="1.0.0",
            description="Slow tool.",
            input_model=SampleInput,
            handler=handler,
            timeout_seconds=60,
        )
    )
    cancel_event = asyncio.Event()

    async def cancel_soon() -> None:
        await handler_started.wait()
        cancel_event.set()

    cancel_task = asyncio.create_task(cancel_soon())
    with pytest.raises(ApplicationError) as exc:
        await registry.invoke_with_meta(
            "slow",
            {"value": "x"},
            cancel_event=cancel_event,
        )
    await cancel_task
    assert exc.value.code == "tool_cancelled"
    # The cancellation reached the handler's await point: its finally ran.
    assert finally_ran is True


async def test_cancel_before_invoke_raises_immediately() -> None:
    registry = ToolRegistry()
    called = False

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"value": "x"}

    registry.register(
        ToolSpec(
            name="quick",
            version="1.0.0",
            description="Quick tool.",
            input_model=SampleInput,
            handler=handler,
        )
    )
    cancel_event = asyncio.Event()
    cancel_event.set()
    with pytest.raises(ApplicationError) as exc:
        await registry.invoke_with_meta(
            "quick",
            {"value": "x"},
            cancel_event=cancel_event,
        )
    assert exc.value.code == "tool_cancelled"
    assert called is False


async def test_cancellation_is_never_retried() -> None:
    registry = ToolRegistry()

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        await asyncio.sleep(60)

    registry.register(
        ToolSpec(
            name="cancellable",
            version="1.0.0",
            description="Cancellable retryable tool.",
            input_model=SampleInput,
            handler=handler,
            max_retries=3,
            idempotent=True,
            timeout_seconds=60,
        )
    )
    cancel_event = asyncio.Event()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.05)
        cancel_event.set()

    cancel_task = asyncio.create_task(cancel_soon())
    with pytest.raises(ApplicationError) as exc:
        await registry.invoke_with_meta(
            "cancellable",
            {"value": "x"},
            cancel_event=cancel_event,
        )
    await cancel_task
    assert exc.value.code == "tool_cancelled"


# ---------- 并发策略 ----------


async def test_max_concurrency_caps_parallel_calls() -> None:
    registry = ToolRegistry()
    active = 0
    peak = 0
    gate = asyncio.Event()

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await gate.wait()
        active -= 1
        return {"value": "x"}

    registry.register(
        ToolSpec(
            name="serial",
            version="1.0.0",
            description="Concurrency-capped tool.",
            input_model=SampleInput,
            handler=handler,
            max_concurrency=1,
        )
    )
    pending = asyncio.gather(
        *(
            registry.invoke("serial", {"value": "x"})
            for _ in range(4)
        )
    )
    # Let the first call take the lock and start its handler, then release
    # the gate so the queued calls run one after the other.
    await asyncio.sleep(0)
    gate.set()
    results = await pending
    assert len(results) == 4
    assert peak == 1


# ---------- 结构化重试记录 ----------


async def test_retry_history_is_structured_and_reported() -> None:
    registry = ToolRegistry()
    attempts = 0
    collected: list[dict[str, object]] = []

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ApplicationError("transient boom", code="transient_failure")
        return {"value": "ok"}

    registry.register(
        ToolSpec(
            name="flaky",
            version="1.0.0",
            description="Flaky retryable tool.",
            input_model=SampleInput,
            handler=handler,
            max_retries=2,
            idempotent=True,
        )
    )
    invocation = await registry.invoke_with_meta(
        "flaky",
        {"value": "x"},
        on_retry=collected.append,
    )
    assert invocation.output == {"value": "ok"}
    assert len(invocation.retry_history) == 2
    assert len(collected) == 2
    first = invocation.retry_history[0]
    assert first["attempt"] == 1
    assert first["error_code"] == "transient_failure"
    assert "transient boom" in str(first["error_message"])
    assert first["delay_seconds"] == 1  # 2 ** (attempt - 1)
    assert invocation.retry_history[1]["attempt"] == 2
    assert invocation.retry_history[1]["delay_seconds"] == 2


async def test_timeout_retries_then_reports_timeout() -> None:
    registry = ToolRegistry()

    async def handler(arguments: BaseModel) -> dict[str, Any]:
        await asyncio.sleep(60)

    registry.register(
        ToolSpec(
            name="hang",
            version="1.0.0",
            description="Hanging tool.",
            input_model=SampleInput,
            handler=handler,
            timeout_seconds=1,
            max_retries=1,
        )
    )
    with pytest.raises(ApplicationError) as exc:
        await registry.invoke("hang", {"value": "x"})
    assert exc.value.code == "tool_timeout"


# ---------- 平台级并发策略（crawl 逐平台 + 每平台信号量） ----------


class CountingCrawler:
    def __init__(self) -> None:
        self.platform_active: dict[str, int] = {}
        self.platform_peaks: dict[str, int] = {}
        self.calls: list[list[str]] = []

    async def collect(self, request: Any) -> list[dict[str, object]]:
        platform = request.platforms[0]
        self.platform_active[platform] = self.platform_active.get(platform, 0) + 1
        self.platform_peaks[platform] = max(
            self.platform_peaks.get(platform, 0),
            self.platform_active[platform],
        )
        self.calls.append(list(request.platforms))
        await asyncio.sleep(0.05)
        posts = [
            {"id": f"{platform}-{index}", "platform": platform, "content": "x"}
            for index in range(2)
        ]
        self.platform_active[platform] -= 1
        return posts


class CrawlerSandboxStub:
    def __init__(self, crawler: Any) -> None:
        self._crawler = crawler

    async def execute(self, *, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        request = CrawlRequest(
            topic=str(payload.get("topic") or ""),
            platforms=list(payload.get("platforms") or []),
            time_range=dict(payload.get("time_range") or {}),
            limit_per_platform=int(payload.get("limit_per_platform") or 150),
            per_day_limit=int(payload.get("per_day_limit") or 150),
            comment_limit=int(payload.get("comment_limit") or 10),
            keywords=dict(payload.get("keywords") or {}),
        )
        posts = await self._crawler.collect(request)
        return {"ok": True, "posts": posts, "platforms": request.platforms}


async def test_crawl_splits_platforms_and_caps_platform_concurrency() -> None:
    crawler = CountingCrawler()
    registry = build_tool_registry(crawler)
    registry.set_sandbox_executor(CrawlerSandboxStub(crawler))

    # 6 concurrent crawl calls on the same platform pair: per-platform
    # concurrency must stay within the policy (2), never 6.
    await asyncio.gather(
        *(
            registry.invoke(
                "collect_social_posts",
                {
                    "topic": "t",
                    "platforms": ["weibo", "bilibili"],
                    "time_range": {},
                },
            )
            for _ in range(3)
        )
    )

    # per-platform concurrency stays within the policy (2)
    assert crawler.platform_peaks["weibo"] <= 2
    assert crawler.platform_peaks["bilibili"] <= 2
    # every platform was collected as a single-platform request
    assert all(len(platforms) == 1 for platforms in crawler.calls)


async def test_crawl_aggregation_matches_batch_result() -> None:
    crawler = DemoCrawlerAdapter()
    registry = build_tool_registry(crawler)
    registry.set_sandbox_executor(
        SandboxedToolExecutor(base_env={"COIFESP_DEMO_MODE": "1"})
    )
    result = await registry.invoke(
        "collect_social_posts",
        {
            "topic": "测试事件",
            "platforms": ["weibo", "bilibili"],
            "time_range": {},
        },
    )
    posts = result["posts"]
    # 每平台返回该平台全部模板（8 weibo + 6 bilibili）。
    assert len(posts) == 14
    assert {post["platform"] for post in posts} == {"weibo", "bilibili"}
    assert [post["platform"] for post in posts].count("weibo") == 8
    assert [post["platform"] for post in posts].count("bilibili") == 6
    # Demo posts include comments and the collection coverage policy keeps
    # the configured default of ten comments per retained post.
    assert result["comment_count"] == 140


# ---------- runtime 取消接线 ----------


async def test_runtime_reports_cancelled_tool_call() -> None:
    tools = ToolRegistry()
    handler_started = asyncio.Event()

    async def slow_handler(arguments: BaseModel) -> dict[str, Any]:
        handler_started.set()
        await asyncio.sleep(60)
        return {"value": "never"}

    tools.register(
        ToolSpec(
            name="slow_probe",
            version="1.0.0",
            description="Slow probe.",
            input_model=SampleInput,
            handler=slow_handler,
            timeout_seconds=120,
        )
    )

    class CancelGateway(LLMGateway):
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
                            id="cancel-call",
                            name="slow_probe",
                            arguments={"value": "x"},
                        )
                    ],
                    model="fake-model",
                )
            return LLMResponse(
                message=LLMMessage(role="assistant", content="recovered"),
                model="fake-model",
            )

    events: list[dict[str, Any]] = []

    async def capture(event: dict[str, Any]) -> None:
        events.append(event)

    runtime = AgentRuntime(
        CancelGateway(),
        tools,
        HookBus(),
        event_sink=capture,
    )
    cancel_event = asyncio.Event()

    async def cancel_soon() -> None:
        await handler_started.wait()
        cancel_event.set()

    cancel_task = asyncio.create_task(cancel_soon())
    result = await runtime.run(
        AgentDefinition(
            name="researcher",
            instructions="Use tools.",
            model_route=ModelRoute.FAST,
            allowed_tools=frozenset({"slow_probe"}),
        ),
        user_message="go",
        system_context="",
        context=RuntimeContext(run_id="run-1", case_id="case-1", turn_id="turn-1"),
        cancel_event=cancel_event,
    )
    await cancel_task

    # The cancelled call is reported, then the loop continues and finishes.
    assert result.content == "recovered"
    end_events = [event for event in events if event["event_type"] == "tool_execution_end"]
    assert end_events[0]["status"] == "cancelled"
    assert end_events[0]["error_code"] == "tool_cancelled"


# ---------- Tool 费用累计（run trace 汇总） ----------


def test_run_trace_accumulates_tool_and_model_costs(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'trace-cost.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/cases",
            json={"topic": "费用累计", "platforms": ["weibo"]},
        )
        case_id = created.json()["id"]
        run = client.post(
            f"/api/v1/cases/{case_id}/messages",
            json={"content": "请分析该案例"},
        ).json()
        run_id = run["id"]

        repo = app.state.container.repository

        async def _seed() -> None:
            await repo.add_model_call(
                call_id="mc-1",
                run_id=run_id,
                model="deepseek-v4-flash",
                route="fast",
                input_tokens=100,
                cached_input_tokens=0,
                output_tokens=50,
                estimated_cost=0.3,
                currency="CNY",
                pricing_model="deepseek-v4-flash",
                latency_ms=1000,
            )
            await repo.add_tool_call(
                call_id="tc-1",
                run_id=run_id,
                tool_name="search_social_evidence",
                skill_name=None,
                status="completed",
                arguments={"case_id": case_id, "query": "q"},
                retry_count=1,
                retry_history=[
                    {
                        "attempt": 1,
                        "error_code": "transient_failure",
                        "error_message": "boom",
                        "delay_seconds": 1,
                    }
                ],
                estimated_cost=0.2,
                idempotency_key=f"{run_id}:call-1",
            )
            await repo.add_tool_call(
                call_id="tc-2",
                run_id=run_id,
                tool_name="get_artifact",
                skill_name=None,
                status="completed",
                arguments={"case_id": case_id},
                cached=True,
                estimated_cost=0.0,
                idempotency_key=f"{run_id}:call-2",
            )

        asyncio.run(_seed())

        trace = client.get(f"/api/v1/runs/{run_id}/trace").json()

    assert trace["model_cost_total"] == 0.3
    assert trace["tool_cost_total"] == 0.2
    assert trace["total_cost"] == 0.5
    tool_calls = {call["id"]: call for call in trace["tool_calls"]}
    assert tool_calls["tc-1"]["retry_history"] == [
        {
            "attempt": 1,
            "error_code": "transient_failure",
            "error_message": "boom",
            "delay_seconds": 1,
        }
    ]
    assert tool_calls["tc-1"]["retry_count"] == 1
    assert tool_calls["tc-2"]["cached"] is True
