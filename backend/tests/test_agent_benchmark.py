"""interview_benchmark_v1 的结构与执行测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.evaluation.agent_dataset import (
    BENCHMARK_CATEGORIES,
    BENCHMARK_SUITE_VERSION,
    load_suite,
    validate_suite,
)
from app.evaluation.agent_eval import AgentSuiteRunner
from app.infrastructure.database import Database


@pytest.fixture(scope="module")
def benchmark_suite():
    return load_suite(suite_version=BENCHMARK_SUITE_VERSION)


def test_benchmark_has_three_fixed_scenarios(benchmark_suite) -> None:
    assert benchmark_suite.suite_version == BENCHMARK_SUITE_VERSION
    assert len(benchmark_suite.tasks) == 3
    assert {task.category for task in benchmark_suite.tasks} == set(BENCHMARK_CATEGORIES)


def test_benchmark_suite_validates(benchmark_suite, tmp_path: Path) -> None:
    problems = validate_suite(
        benchmark_suite,
        enforce_counts=False,
        allowed_categories=BENCHMARK_CATEGORIES,
    )
    assert problems == []


def test_benchmark_fixtures_meet_plan_requirements(benchmark_suite) -> None:
    """B1 需要 2 平台 30-50 帖与 >=3 结论；B2 需要 3 调查；B3 需要 >12 证据引用。"""
    b1 = benchmark_suite.fixtures["bench_b1"]["investigations"][0]
    assert len(b1["posts"]) >= 30
    assert {post["platform"] for post in b1["posts"]} == {"weibo", "bilibili"}
    assert len(b1["findings"]) >= 3

    b2 = benchmark_suite.fixtures["bench_b2"]
    assert len(b2["investigations"]) == 3
    assert any(link["status"] == "observed" for link in b2["cross_links"])
    assert any(link["status"] == "candidate" for link in b2["cross_links"])
    # 共享媒体：两个调查挂同一 normalized_url + file_sha256
    media_hashes = [
        media.get("file_sha256")
        for inv in b2["investigations"]
        for media in inv.get("media", [])
    ]
    assert len(media_hashes) == 2
    assert media_hashes[0] == media_hashes[1]

    b3 = benchmark_suite.fixtures["bench_b3"]["investigations"][0]
    assert len(b3["findings"]) == 1
    assert len(b3["findings"][0]["evidence_refs"]) > 12
    relations = {ref["relation"] for ref in b3["findings"][0]["evidence_refs"]}
    assert relations == {"supports", "contradicts", "context"}


def test_benchmark_scenario_runs_end_to_end(tmp_path: Path) -> None:
    """B2（跨调查）端到端执行：验证基准场景真的能跑在真实 runtime 上。"""

    async def _run():
        database = Database(f"sqlite+aiosqlite:///{tmp_path / 'bench.db'}")
        await database.create_schema()
        try:
            runner = AgentSuiteRunner(
                suite=load_suite(suite_version=BENCHMARK_SUITE_VERSION),
                database=database,
                candidate_label="test",
            )
            return await runner.run(task_ids=["B2_cross_investigation"])
        finally:
            await database.dispose()

    import asyncio

    report = asyncio.run(_run())
    result = report.task_results[0]
    assert result.status == "completed"
    assert result.succeeded, [item["evaluator"] for item in result.failure_details]
    assert result.metric("agent.required_tool_coverage") == 1.0
    assert report.sample_size == 1


def test_benchmark_reports_carry_context(benchmark_suite) -> None:
    """报告必须带 context（计划第 57 节）：禁止裸百分比。"""
    from app.scripts.run_agent_benchmark import CORE_METRICS, render_markdown
    from app.evaluation.agent_eval import AgentEvaluationReport

    report = AgentEvaluationReport(
        suite_version=benchmark_suite.suite_version,
        mode="contract",
        candidate_label="t",
        candidate_version="t",
        git_sha="deadbeef",
        started_at="2026-09-16T00:00:00+00:00",
        finished_at="2026-09-16T00:01:00+00:00",
        sample_size=3,
        metrics={"agent.task_success_rate": 1.0},
        task_results=(),
    )
    markdown = render_markdown(report, model_label="scripted", baseline_compare=None, failures=[])
    assert "sample size" in markdown
    assert "git SHA" in markdown
    assert "`deadbeef`" in markdown
    assert "suite version" in markdown
    assert "interview_benchmark_v1" in markdown
    assert "agent.task_success_rate" in markdown
