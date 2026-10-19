from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.evaluation.agent_dataset import AgentGoldenSuite, load_suite
from app.evaluation.agent_eval import (
    CONTRACT_MODE,
    AgentSuiteRunner,
    _summarize_gateway_measurements,
)
from app.evaluation.agent_trace import ToolCallView
from app.evaluation.investigation_real_eval import (
    EXACT_DB_READ_TOOLS,
    FAULT_PERSISTENT_READ,
    FAULT_TRANSIENT_READ,
    INVESTIGATION_REAL_SUITE,
    EvalToolRegistry,
    process_metrics,
)


class RecordingSink:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def record_tool_call(self, **payload: object) -> None:
        self.items.append(dict(payload))

    def observations(self) -> tuple[dict[str, object], ...]:
        return tuple(self.items)


class ReadOnlySpec:
    side_effect = "none"
    permissions = ("read_database",)


class FakeRegistry:
    def get(self, name: str) -> ReadOnlySpec:
        return ReadOnlySpec()

    async def invoke_with_meta(
        self, name: str, arguments: dict[str, object], **kwargs: object
    ) -> object:
        return SimpleNamespace(output={"rows": [name]}, duration_ms=1)


@pytest.mark.asyncio
async def test_transient_fault_is_one_shot_and_recorded() -> None:
    recorder = RecordingSink()
    tools = EvalToolRegistry(
        FakeRegistry(),
        task_tags=(FAULT_TRANSIENT_READ,),
        observation_recorder=recorder,
        enable_faults=True,
    )
    context = {"security_context": {"tool_call_id": "call-first"}}

    with pytest.raises(Exception, match="Controlled evaluation read fault") as caught:
        await tools.invoke_with_meta("query_social_posts", {}, **context)
    assert caught.value.code == "eval_injected_transient_read"

    recovered = await tools.invoke_with_meta(
        "query_social_posts", {},
        security_context={"tool_call_id": "call-second"},
    )
    assert recovered.output == {"rows": ["query_social_posts"]}
    assert recorder.items[0]["status"] == "failed"
    assert recorder.items[0]["error_code"] == "eval_injected_transient_read"
    assert recorder.items[1]["status"] == "succeeded"
    assert tools.fault_summary()["injected_failure_count"] == 1


@pytest.mark.asyncio
async def test_persistent_fault_covers_all_exact_db_read_alternatives() -> None:
    recorder = RecordingSink()
    tools = EvalToolRegistry(
        FakeRegistry(),
        task_tags=(FAULT_PERSISTENT_READ,),
        observation_recorder=recorder,
        enable_faults=True,
    )
    for index, tool in enumerate(sorted(EXACT_DB_READ_TOOLS)):
        with pytest.raises(Exception) as caught:
            await tools.invoke_with_meta(
                tool,
                {},
                security_context={"tool_call_id": f"call-{index}"},
            )
        assert caught.value.code == "eval_injected_persistent_read"
    assert len(recorder.items) == len(EXACT_DB_READ_TOOLS)
    assert all(item["error_code"] == "eval_injected_persistent_read" for item in recorder.items)


def test_usage_and_cost_unknown_are_not_reported_as_zero_or_usd() -> None:
    summary = _summarize_gateway_measurements(
        [
            {
                "input_tokens": None,
                "output_tokens": None,
                "duration_ms": 12,
                "cost": None,
                "currency": "CNY",
                "pricing_status": "NOT_MEASURABLE",
            }
        ]
    )
    assert summary["input_tokens"] is None
    assert summary["output_tokens"] is None
    assert summary["usage_status"] == "NOT_MEASURABLE"
    assert summary["estimated_cost_usd"] is None
    assert summary["cost_currency"] is None
    assert summary["cost_status"] == "NOT_MEASURABLE"
    assert summary["model_request_duration_ms"] == 12


def test_process_metrics_counts_failed_and_unproductive_repeated_calls() -> None:
    calls = (
        ToolCallView(
            id="call-1",
            tool="query_social_posts",
            status="failed",
            arguments={"case_id": "case-1", "limit": 20},
            error_code="eval_injected_persistent_read",
        ),
        ToolCallView(
            id="call-2",
            tool="query_social_posts",
            status="failed",
            arguments={"limit": 20, "case_id": "case-1"},
            error_code="eval_injected_persistent_read",
        ),
    )
    summary = process_metrics(tool_calls=calls, observations=())
    assert summary["tool_failure_counts"] == {
        "total": 2,
        "by_tool": {"query_social_posts": 2},
        "by_error_code": {"eval_injected_persistent_read": 2},
    }
    assert summary["unproductive_repeat_count"] == 1


@pytest.mark.asyncio
async def test_transient_failure_is_persisted_in_real_agent_trace() -> None:
    legacy = load_suite(suite_version="interview_agent_v1")
    source_task = legacy.by_id("G1_01")
    task = replace(
        source_task,
        id="IR09_HARNESS_TRACE",
        suite_version=INVESTIGATION_REAL_SUITE,
        tags=(FAULT_TRANSIENT_READ,),
        expected=replace(
            source_task.expected,
            required_tools=("query_social_posts",),
            required_tool_groups=(),
            expected_tool_result_contains=(),
        ),
        budgets=replace(
            source_task.budgets,
            task_timeout_seconds=30,
            max_agent_steps=4,
            max_tool_calls=2,
        ),
    )
    suite = AgentGoldenSuite(
        suite_version=INVESTIGATION_REAL_SUITE,
        manifest={"suite_version": INVESTIGATION_REAL_SUITE},
        tasks=(task,),
        fixtures=legacy.fixtures,
        root=legacy.root,
    )
    result = (
        await AgentSuiteRunner(suite=suite, database=None, mode=CONTRACT_MODE).run()
    ).task_results[0]

    assert result.trace_bundle["tool_calls"][0]["tool"] == "query_social_posts"
    assert result.trace_bundle["tool_calls"][0]["status"] == "failed"
    assert result.trace_bundle["tool_calls"][0]["error_code"] == "eval_injected_transient_read"
    assert result.trace_bundle["fault_injection"]["injected_failure_count"] == 1
    assert result.trace_bundle["observations"][0]["error_code"] == "eval_injected_transient_read"
    assert result.task_database_path is not None
    assert await asyncio.to_thread(os.path.isdir, result.task_database_path)
    assert "task_databases" in result.task_database_path


@pytest.mark.asyncio
async def test_gateway_hard_stop_marks_remaining_real_tasks_not_run() -> None:
    legacy = load_suite(suite_version="interview_agent_v1")
    tasks = tuple(
        replace(task, suite_version=INVESTIGATION_REAL_SUITE)
        for task in legacy.tasks[:2]
    )
    suite = AgentGoldenSuite(
        suite_version=INVESTIGATION_REAL_SUITE,
        manifest={"suite_version": INVESTIGATION_REAL_SUITE},
        tasks=tasks,
        fixtures=legacy.fixtures,
        root=legacy.root,
    )
    gateway = SimpleNamespace(hard_stop_reason="provider returned HTTP 401")
    report = await AgentSuiteRunner(
        suite=suite, database=None, mode="real_model", gateway=gateway
    ).run()

    assert [result.status for result in report.task_results] == ["blocked", "blocked"]
    assert all(result.run_id == "" for result in report.task_results)
    assert all("BLOCKED_NOT_RUN" in (result.error or "") for result in report.task_results)
    assert report.metrics["agent.tasks_blocked"] == 2
    assert report.metrics["agent.tasks_selected"] == 2
    assert report.metrics["agent.tasks_attempted"] == 0
    assert report.metrics["agent.task_success_rate"] == 0
    assert not report.passed
    assert any(
        item["metric"] == "agent.gateway_hard_stop"
        for item in report.hard_gate_violations
    )
