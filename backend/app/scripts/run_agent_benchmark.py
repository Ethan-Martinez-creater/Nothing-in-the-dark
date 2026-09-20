"""interview_benchmark_v1 运行入口（主计划 Phase 6 / 第 54–58 节）。

用法::

    python -m app.scripts.run_agent_benchmark --mode contract
    python -m app.scripts.run_agent_benchmark --mode real_model \
        --candidate-label v1-baseline --baseline-id <baseline_label>

产出::

    artifacts/benchmark/<timestamp>/
    ├── report.json      完整指标 + failure analysis
    ├── report.md        可读报告（带 sample size / suite / model / git SHA / date）
    └── traces/          每个场景的 Replay Bundle（已脱敏）

三个固定场景：B1 Grounded Investigation / B2 Cross-Investigation /
B3 Adversarial Review。全部使用冻结 fixture，执行时绝不联网。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.evaluation.agent_dataset import BENCHMARK_SUITE_VERSION, load_suite
from app.evaluation.agent_eval import (
    CONTRACT_MODE,
    REAL_MODEL_MODE,
    AgentEvaluationReport,
    AgentSuiteRunner,
    _git_sha,
)
from app.evaluation.agent_eval import _prompt_hash  # noqa: PLC2701 - 同包内部工具

BENCHMARK_DIR = Path("artifacts/benchmark")

#: 核心指标清单（计划第 56 节）。
CORE_METRICS: tuple[str, ...] = (
    "agent.task_success_rate",
    "agent.required_tool_coverage",
    "agent.tool_argument_accuracy",
    "agent.invalid_citation_count",
    "agent.unexpected_mutation_count",
    "agent.human_escalation_accuracy",
    "agent.avg_steps",
    "agent.avg_tool_calls",
    "agent.p50_latency_ms",
    "agent.p95_latency_ms",
    "agent.avg_input_tokens",
    "agent.avg_output_tokens",
    "agent.avg_cost_usd",
    "agent.judge_relevance",
    "agent.judge_completeness",
    "agent.judge_uncertainty",
)

#: Failure Analysis 的固定分类（计划第 58 节）。
FAILURE_CATEGORIES: tuple[str, ...] = (
    "routing",
    "argument",
    "grounding",
    "citation",
    "state mutation",
    "uncertainty",
    "latency",
    "model reasoning",
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run interview_benchmark_v1")
    parser.add_argument("--suite", default=BENCHMARK_SUITE_VERSION)
    parser.add_argument("--mode", choices=[CONTRACT_MODE, REAL_MODEL_MODE], default=CONTRACT_MODE)
    parser.add_argument("--candidate-label", default="candidate")
    parser.add_argument("--baseline-id", default="", help="对比用的 baseline 标签")
    parser.add_argument("--output", default="", help="输出目录（默认 artifacts/benchmark/<ts>）")
    parser.add_argument("--tasks", default="", help="逗号分隔的场景 id 子集")
    parser.add_argument("--fail-on-hard-gate", action="store_true")
    return parser


def _resolve_output_dir(explicit: str) -> Path:
    if explicit:
        return Path(explicit)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return BENCHMARK_DIR / stamp


def _build_gateway() -> Any:
    from app.bootstrap import ApplicationContainer
    from app.core.config import get_settings

    container = ApplicationContainer(get_settings())
    gateway = container.llm
    if not getattr(gateway, "configured", False):
        raise SystemExit(
            "real_model mode requires a configured LLM gateway (set LLM_API_KEY); "
            "refusing to fabricate benchmark numbers."
        )
    return gateway


def failure_analysis(report: AgentEvaluationReport) -> list[dict[str, object]]:
    """按计划第 58 节的字段输出失败分析。"""
    items: list[dict[str, object]] = []
    for result in report.task_results:
        for detail in result.failure_details:
            trace = result.trace_bundle
            items.append(
                {
                    "task_id": result.task_id,
                    "scenario": result.category,
                    "category": detail.get("failure_category") or "model reasoning",
                    "evaluator": detail.get("evaluator"),
                    "metric": detail.get("metric"),
                    "expected": _expected_summary(detail),
                    "actual": _actual_summary(result, detail),
                    "tool_trace": [
                        {"tool": call.get("tool"), "status": call.get("status")}
                        for call in trace.get("tool_calls", [])
                    ],
                    "root_cause": _root_cause(detail),
                    "details": detail.get("details"),
                }
            )
    return items


def _expected_summary(detail: dict[str, object]) -> dict[str, object]:
    if detail.get("evaluator") == "E2_required_tool_coverage":
        return {"required_tools": (detail.get("details") or {}).get("required")}
    if detail.get("evaluator") == "E4_tool_argument_correctness":
        return {"valid_tool_arguments": True}
    if detail.get("evaluator") == "E5_state_mutation":
        return {"unexpected_mutations": 0}
    if detail.get("evaluator") == "E1_task_completion":
        return {"all_state_assertions_met": True}
    return {}


def _actual_summary(result: Any, detail: dict[str, object]) -> dict[str, object]:
    details = detail.get("details") or {}
    summary: dict[str, object] = {
        "run_status": result.status,
        "value": detail.get("value"),
    }
    if "missing" in details:
        summary["missing"] = details["missing"]
    if "problem_calls" in details:
        summary["problem_calls"] = details["problem_calls"]
    if "unexpected" in details:
        summary["unexpected"] = details["unexpected"]
    if "assertion_failures" in details:
        summary["assertion_failures"] = details["assertion_failures"]
    return summary


def _root_cause(detail: dict[str, object]) -> str:
    category = detail.get("failure_category")
    mapping = {
        "routing": "工具选择或委派路径与期望不一致（含未调用必需工具/调用了禁止工具）",
        "argument": "工具参数不符合 schema 或未遵守 case scope / 有界性约束",
        "grounding": "结论缺少可解析的证据支撑，或未使用期望证据",
        "citation": "回答中出现了数据库中不存在的 id（幻觉引用）",
        "state mutation": "产生了非预期的状态变更（结论状态/评审状态/计数）",
        "uncertainty": "在数据不足时未明确说明或出现了外推",
        "latency": "超出任务预算（步数/工具调用/延迟）",
        "model reasoning": "模型决策或产出物与期望不符",
    }
    return mapping.get(str(category), "未分类失败")


def render_markdown(
    report: AgentEvaluationReport,
    *,
    model_label: str,
    baseline_compare: dict[str, object] | None,
    failures: list[dict[str, object]],
) -> str:
    metrics = report.metrics
    lines = [
        "# Canonical Agent Benchmark — interview_benchmark_v1",
        "",
        "> 三个固定场景（B1 Grounded Investigation / B2 Cross-Investigation / "
        "B3 Adversarial Review），全部使用冻结 fixture，执行时不联网。",
        "",
        "## 运行上下文（禁止只看裸百分比）",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| suite version | `{report.suite_version}` |",
        f"| mode | `{report.mode}` |",
        f"| candidate | `{report.candidate_label}` |",
        f"| model | `{model_label}` |",
        f"| git SHA | `{report.git_sha}` |",
        f"| date | {report.started_at} |",
        f"| sample size | {report.sample_size} |",
        f"| hard gates | {'PASS' if report.passed else 'BLOCK'} |",
        "",
        "## 场景结果",
        "",
        "| 场景 | 任务 | 状态 | 通过 | 失败分类 |",
        "|---|---|---|---|---|",
    ]
    for result in report.task_results:
        lines.append(
            f"| {result.category} | `{result.task_id}` | {result.status} | "
            f"{'Y' if result.succeeded else 'N'} | {', '.join(result.failure_categories) or '—'} |"
        )

    lines += ["", "## 核心指标", "", "| metric | value |", "|---|---|"]
    for key in CORE_METRICS:
        if key in metrics:
            lines.append(f"| `{key}` | {metrics[key]} |")
    extra = [key for key in sorted(metrics) if key not in CORE_METRICS]
    for key in extra:
        lines.append(f"| `{key}` | {metrics[key]} |")

    if baseline_compare is not None:
        lines += [
            "",
            "## 与 baseline 对比",
            "",
            f"- baseline: `{baseline_compare.get('baseline_id') or '—'}`",
            f"- passed: `{baseline_compare.get('passed')}`",
            "",
        ]
        checks = baseline_compare.get("checks") or []
        if checks:
            lines += ["| metric | baseline | candidate | limit | passed |", "|---|---|---|---|---|"]
            for item in checks:  # type: ignore[union-attr]
                lines.append(
                    f"| `{item['metric']}` | {item['baseline']} | {item['candidate']} | "
                    f"{item['limit']} | {'Y' if item['passed'] else 'N'} |"
                )

    if failures:
        lines += ["", "## Failure Analysis", ""]
        for item in failures:
            lines += [
                f"### {item['task_id']} — {item['evaluator']}",
                "",
                f"- 分类：`{item['category']}`",
                f"- 期望：`{json.dumps(item['expected'], ensure_ascii=False)}`",
                f"- 实际：`{json.dumps(item['actual'], ensure_ascii=False)}`",
                f"- 工具轨迹：`{json.dumps(item['tool_trace'], ensure_ascii=False)}`",
                f"- 根因判断：{item['root_cause']}",
                "",
            ]
    else:
        lines += ["", "## Failure Analysis", "", "本次运行没有失败任务。", ""]

    if report.limitations:
        lines += ["## 已知限制", ""]
        lines += [f"- {item}" for item in report.limitations]
        lines.append("")
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> int:
    from app.infrastructure.database import Database
    import tempfile

    suite = load_suite(suite_version=args.suite)
    tmp = Path(tempfile.mkdtemp(prefix="agent-benchmark-"))
    database = Database(f"sqlite+aiosqlite:///{tmp / 'benchmark.db'}")
    await database.create_schema()

    gateway = None
    model_label = "contract-scripted-model"
    if args.mode == REAL_MODEL_MODE:
        gateway = _build_gateway()
        model_label = "production-gateway"

    try:
        runner = AgentSuiteRunner(
            suite=suite,
            database=database,
            mode=args.mode,
            gateway=gateway,
            candidate_label=args.candidate_label,
            # B2 跨调查场景要求一个 scenario 一个 DB（修复计划 7.3），
            # 不做 per-task 隔离。
            per_task_isolation=False,
        )
        task_ids = [item for item in args.tasks.split(",") if item.strip()] or None
        report = await runner.run(task_ids=task_ids, candidate_version=args.candidate_label)
    finally:
        await database.dispose()

    baseline_compare: dict[str, object] | None = None
    if args.baseline_id:
        baseline_path = BENCHMARK_DIR / "baseline.json"
        if baseline_path.exists():
            baseline_payload = json.loads(baseline_path.read_text(encoding="utf-8"))
            from app.application.agent_evaluation_service import AgentEvaluationService

            service = AgentEvaluationService.__new__(AgentEvaluationService)
            baseline_compare = service.compare_to_baseline(
                report, dict(baseline_payload.get("metrics") or {})
            )
            baseline_compare["baseline_id"] = args.baseline_id
        else:
            print(
                f"baseline {args.baseline_id!r} requested but {baseline_path} not found; "
                "skipping comparison",
                file=sys.stderr,
            )

    failures = failure_analysis(report)
    output_dir = _resolve_output_dir(args.output)
    traces_dir = output_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    payload = report.to_dict()
    payload["model"] = model_label
    payload["failure_analysis"] = failures
    payload["core_metrics"] = {key: report.metrics[key] for key in CORE_METRICS if key in report.metrics}
    if baseline_compare is not None:
        payload["baseline_compare"] = baseline_compare

    (output_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        render_markdown(
            report,
            model_label=model_label,
            baseline_compare=baseline_compare,
            failures=failures,
        ),
        encoding="utf-8",
    )

    # 每个场景的 Replay Bundle（已脱敏），供 Phase 4 回放与人工审查。
    for result in report.task_results:
        bundle = {
            "task_id": result.task_id,
            "scenario": result.category,
            "case_id": result.case_id,
            "run_id": result.run_id,
            "status": result.status,
            "objective": result.trace_bundle.get("objective"),
            "final_answer": result.trace_bundle.get("final_answer"),
            "tool_calls": result.trace_bundle.get("tool_calls"),
            "artifacts": result.trace_bundle.get("artifacts"),
            "metrics": result.trace_bundle.get("metrics"),
            "failure_categories": list(result.failure_categories),
            "prompt_hash": _prompt_hash(suite.by_id(result.task_id)),
            "git_sha": _git_sha(),
            "suite_version": report.suite_version,
            "redacted": True,
        }
        from app.evaluation.agent_manifest import redact

        (traces_dir / f"{result.task_id}.json").write_text(
            json.dumps(redact(bundle), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"benchmark report written to {output_dir}")
    print(f"sample_size={report.sample_size} hard_gates={'PASS' if report.passed else 'BLOCK'}")
    for key in CORE_METRICS:
        if key in report.metrics:
            print(f"  {key} = {report.metrics[key]}")

    if args.fail_on_hard_gate and not report.passed:
        print("hard gate violated", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    args = _build_arg_parser().parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
