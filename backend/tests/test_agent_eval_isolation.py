"""FC-IR-01 / FC-IR-05 / FC-IR-03 的专项测试矩阵。

* IR-TOOL-01..04：DB / Intelligence Tool 真实读取冻结 fixture（非 unavailable）。
* IR-ISO-01/02：per-task DB 隔离——任务顺序不影响 observation 与指标。
* IR-GATE-01..03：CLI/service 真正走 evaluation_runs → 现有 Release Gate →
  baseline/candidate regression 主链。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.evaluation.agent_dataset import load_suite
from app.evaluation.agent_eval import AgentSuiteRunner
from app.infrastructure.database import Database


def _run_tasks(tmp_path: Path, task_ids: list[str], name: str = "eval.db"):
    """共享 DB 跑指定任务（返回 database, runner, report）。"""

    async def _run():
        database = Database(f"sqlite+aiosqlite:///{tmp_path / name}")
        await database.create_schema()
        try:
            runner = AgentSuiteRunner(suite=load_suite(), database=database)
            report = await runner.run(task_ids=task_ids)
            return database, runner, report
        except Exception:
            await database.dispose()
            raise

    return asyncio.run(_run())


def _observation_payloads(runner: AgentSuiteRunner) -> list[dict]:
    """把 recorder 的 observation 文本解析回 JSON 对象。"""
    assert runner._read_recorder is not None  # noqa: SLF001 - 评测内部断言
    payloads: list[dict] = []
    for text in runner._read_recorder.texts():  # noqa: SLF001
        try:
            payloads.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return payloads


# ---------------------------------------------------------------------------
# IR-TOOL：真实 Tool Stack
# ---------------------------------------------------------------------------


def test_ir_tool_01_overview_returns_real_counts(tmp_path: Path) -> None:
    """G1_01 的 get_case_data_overview 必须返回 fixture 的真实数量。"""
    database, runner, report = _run_tasks(tmp_path, ["G1_01"])
    try:
        assert report.task_results[0].status == "completed"
        payloads = _observation_payloads(runner)
        overview = next(p for p in payloads if "counts" in p)
        assert overview["ok"] is True
        # case_grounding fixture：10 帖（fixtures.json 冻结值）。
        assert overview["counts"]["posts"] == 10
        platforms = {
            item["platform"] for item in overview["posts_by_platform"]
        }
        assert {"weibo", "bilibili"} <= platforms
        assert "database_query_unavailable" not in json.dumps(
            payloads, ensure_ascii=False
        )
    finally:
        asyncio.run(database.dispose())


def test_ir_tool_02_query_posts_contains_fixture_fact(tmp_path: Path) -> None:
    """G1_02 的 query_social_posts 必须真的返回含「12 倍」的帖子。"""
    database, runner, report = _run_tasks(tmp_path, ["G1_02"])
    try:
        assert report.task_results[0].status == "completed"
        payloads = _observation_payloads(runner)
        posts_payload = next(p for p in payloads if "posts" in p)
        contents = json.dumps(posts_payload, ensure_ascii=False)
        assert "12 倍" in contents
    finally:
        asyncio.run(database.dispose())


def test_ir_tool_03_workspace_entities_contain_fixture_actor(tmp_path: Path) -> None:
    """G4_02 的 query_workspace_entities 必须返回「热点搬运工」。"""
    database, runner, report = _run_tasks(tmp_path, ["G4_02"])
    try:
        assert report.task_results[0].status == "completed"
        payloads = _observation_payloads(runner)
        entity_payload = next(p for p in payloads if "items" in p)
        names = {
            item.get("canonical_name")
            for item in entity_payload.get("items", [])
        }
        assert "热点搬运工" in names
    finally:
        asyncio.run(database.dispose())


def test_ir_tool_04_related_and_signals_are_real(tmp_path: Path) -> None:
    """G4_01 / G4_03 必须得到 fixture 数据而不是 unavailable。

    注意：recorder 是 runner 级、每任务重建，因此两个任务分别跑，
    各自断言自己的 observation。
    """
    database, runner, report = _run_tasks(tmp_path, ["G4_01"])
    try:
        assert report.task_results[0].status == "completed"
        payloads = _observation_payloads(runner)
        related = next(p for p in payloads if "related_investigations" in p)
        # case_cross fixture：3 调查 + 2 cross link → 当前 case 有关联。
        assert related["total"] >= 1
        assert related["related_investigations"]
        blob = json.dumps(payloads, ensure_ascii=False)
        assert "intelligence_query_unavailable" not in blob
    finally:
        asyncio.run(database.dispose())

    database, runner, report = _run_tasks(tmp_path, ["G4_03"], name="eval2.db")
    try:
        assert report.task_results[0].status == "completed"
        payloads = _observation_payloads(runner)
        signals = next(p for p in payloads if "signals" in p)
        assert signals["total"] >= 1
        blob = json.dumps(payloads, ensure_ascii=False)
        assert "intelligence_query_unavailable" not in blob
        assert "database_query_unavailable" not in blob
    finally:
        asyncio.run(database.dispose())


# ---------------------------------------------------------------------------
# IR-ISO：per-task DB 隔离
# ---------------------------------------------------------------------------


def _normalize_observation(payloads: list[dict]) -> list[dict]:
    """剥离运行期 id（uuid 每库重新生成），只留语义内容做等价比较。"""

    def _strip(value):
        if isinstance(value, dict):
            return {
                key: _strip(item)
                for key, item in value.items()
                if key not in {"id", "case_id", "entity_ids", "related_case_ids",
                               "detected_at", "computed_at", "updated_at"}
            }
        if isinstance(value, list):
            return [_strip(item) for item in value]
        return value

    return [_strip(payload) for payload in payloads]


def test_ir_iso_01_task_order_does_not_change_observation(tmp_path: Path) -> None:
    """先跑 G4_02 与夹在 G4_01/G4_03 之后跑，observation 必须一致。"""
    database, runner, report = _run_tasks(
        tmp_path, ["G4_02", "G4_01", "G4_03", "G4_02"]
    )
    try:
        assert all(r.status == "completed" for r in report.task_results)
        # runner 的 recorder 只保留最后一个任务（G4_02 第二次）的观察；
        # 两次 G4_02 的结果等价性通过 E 系列 outcome + 关键事实双重断言。
        g4_02_runs = [r for r in report.task_results if r.task_id == "G4_02"]
        assert len(g4_02_runs) == 2
        for result in g4_02_runs:
            by_name = {o.evaluator: o for o in result.outcomes}
            assert by_name["E12_answer_grounding"].passed is True
            assert by_name["E12_answer_grounding"].details["grounded"] == [
                "热点搬运工"
            ]
            assert by_name["E2_required_tool_coverage"].passed is True
        # 两次运行的 metric 值必须完全一致（隔离 ⇒ 无累积漂移）。
        first = {o.evaluator: o.value for o in g4_02_runs[0].outcomes}
        second = {o.evaluator: o.value for o in g4_02_runs[1].outcomes}
        for name, value in first.items():
            if name == "E9_efficiency":
                continue  # latency/token 允许不同
            assert second[name] == value, f"{name} drifted: {value} != {second[name]}"
    finally:
        asyncio.run(database.dispose())


def test_ir_iso_02_repeated_tasks_metrics_are_stable(tmp_path: Path) -> None:
    """同一 suite 内重复执行任务，deterministic 指标不得因累积数据漂移。"""
    database, runner, report = _run_tasks(
        tmp_path, ["G1_01", "G4_02", "G1_01", "G4_02"]
    )
    try:
        assert all(r.status == "completed" for r in report.task_results)
        for task_id in ("G1_01", "G4_02"):
            runs = [r for r in report.task_results if r.task_id == task_id]
            assert len(runs) == 2
            first = {
                o.evaluator: o.value
                for o in runs[0].outcomes
                if o.evaluator != "E9_efficiency"
            }
            second = {
                o.evaluator: o.value
                for o in runs[1].outcomes
                if o.evaluator != "E9_efficiency"
            }
            assert first == second, f"{task_id} metrics drifted across repeats"
    finally:
        asyncio.run(database.dispose())


# ---------------------------------------------------------------------------
# IR-GATE：CLI / service 主链
# ---------------------------------------------------------------------------


def test_ir_gate_01_cli_persists_evaluation_run_and_gate_result(
    tmp_path: Path,
) -> None:
    """IR-GATE-01：完整主链——run_agent_suite → evaluation_runs → gate 判定。"""
    from app.application.agent_evaluation_service import AgentEvaluationService
    from app.application.evaluation_service import EvaluationService
    from app.application.repositories import ApplicationRepository

    async def _run():
        database = Database(f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}")
        await database.create_schema()
        try:
            service = AgentEvaluationService(database)
            report = await service.run_agent_suite(
                "interview_agent_v1", task_ids=["G1_01", "G4_02"]
            )
            persisted = await service.persist_report(report, role="baseline")
            repository = ApplicationRepository(database)
            await repository.create_release_gate(
                AgentEvaluationService.default_gate_definition(
                    report.suite_version
                )
            )
            gate_results = await EvaluationService(repository).evaluate_gates(
                str(persisted["run_id"])
            )
            run_record = await repository.get_evaluation_run(
                str(persisted["run_id"])
            )
            return report, gate_results, run_record
        finally:
            await database.dispose()

    report, gate_results, run_record = asyncio.run(_run())
    # evaluation_runs 持久化：聚合指标落库。
    assert run_record.status in {"completed", "partial_failed"}
    assert run_record.aggregate["agent.task_success_rate"] == 1.0
    assert run_record.config["role"] == "baseline"
    assert run_record.config["tool_schema_hash"] == report.tool_schema_hash
    assert (
        run_record.config["coordinator_prompt_hash"]
        == report.coordinator_prompt_hash
    )
    # 既有 Release Gate 真实执行：对每个启用 gate 产出判定记录。
    assert gate_results
    assert all(item["decision"] in {"pass", "block"} for item in gate_results)


def test_ir_gate_02_hard_gate_failure_blocks_exit(tmp_path: Path) -> None:
    """IR-GATE-02：agent hard gate 违规时 CLI 必须 exit 1（不得假绿）。"""
    from app.evaluation.agent_eval import AgentEvaluationReport
    from app.scripts import run_agent_eval

    failing_report = AgentEvaluationReport(
        suite_version="interview_agent_v1",
        mode="contract",
        candidate_label="candidate",
        candidate_version="candidate",
        git_sha="test",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:01:00+00:00",
        sample_size=24,
        metrics={"agent.task_success_rate": 0.5},
        task_results=(),
        hard_gate_violations=(
            {"metric": "agent.critical_task_success_rate", "value": 0.5},
        ),
    )

    async def _fake_suite(self, *args, **kwargs):  # noqa: ANN001, ANN202
        return failing_report

    from app.application.agent_evaluation_service import AgentEvaluationService

    original = AgentEvaluationService.run_agent_suite
    AgentEvaluationService.run_agent_suite = _fake_suite
    try:
        exit_code = asyncio.run(
            run_agent_eval._run(  # noqa: SLF001 - 测 CLI 退出码语义
                run_agent_eval._build_arg_parser().parse_args(  # noqa: SLF001
                    [
                        "--mode",
                        "contract",
                        "--fail-on-hard-gate",
                        "--database-url",
                        f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}",
                    ]
                )
            )
        )
    finally:
        AgentEvaluationService.run_agent_suite = original
    assert exit_code == 1


def test_ir_gate_03_baseline_candidate_regression_compare(tmp_path: Path) -> None:
    """IR-GATE-03：baseline 落库后 candidate 必须真实执行 regression compare。"""
    from app.application.agent_evaluation_service import AgentEvaluationService

    async def _run():
        database = Database(f"sqlite+aiosqlite:///{tmp_path / 'reg.db'}")
        await database.create_schema()
        try:
            service = AgentEvaluationService(database)
            baseline_report = await service.run_agent_suite(
                "interview_agent_v1", task_ids=["G1_01", "G4_02"]
            )
            await service.persist_report(baseline_report, role="baseline")
            loaded = await service.load_baseline_metrics("interview_agent_v1")
            return service, baseline_report, loaded
        finally:
            await database.dispose()

    service, baseline_report, loaded = asyncio.run(_run())
    # baseline 角色过滤：只有 role=baseline 的 run 被加载。
    assert loaded["agent.task_success_rate"] == 1.0
    # 同等 candidate：无回归。
    same = service.compare_to_baseline(baseline_report, loaded)
    assert same["passed"] is True
    # 恶化 candidate：成功率掉 50pp 必须被 max_drop 拦截。
    from dataclasses import replace as dc_replace

    degraded = dc_replace(
        baseline_report,
        metrics={**baseline_report.metrics, "agent.task_success_rate": 0.5},
    )
    compared = service.compare_to_baseline(degraded, loaded)
    assert compared["passed"] is False
    assert any(
        item["metric"] == "agent.task_success_rate"
        for item in compared["violations"]
    )
