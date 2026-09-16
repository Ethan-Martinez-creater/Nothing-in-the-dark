"""Agent Golden Suite 的运行入口（Tier A contract / Tier B real_model）。

用法::

    # Tier A：scripted model，无需任何 API key（PR CI 使用）
    python -m app.scripts.run_agent_eval --mode contract \
        --output artifacts/agent_eval/contract.json

    # Tier B：真实生产模型（manual / nightly；需要 LLM_API_KEY）
    python -m app.scripts.run_agent_eval --mode real_model \
        --candidate-label v1-baseline \
        --output artifacts/agent_eval/baseline.json

设计约束（主计划第 19–24 节）：

* 两种模式都跑**同一个 suite 与同一份期望**，只是模型来源不同；
* 真实模式复用生产 ``OpenAICompatibleGateway``，不另建 gateway；
* 报告始终带 suite / mode / model / git SHA / 样本量，禁止裸百分比；
* hard gate 违规时进程退出码为 1（CI 可直接用）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.evaluation.agent_dataset import load_suite
from app.evaluation.agent_eval import (
    CONTRACT_MODE,
    REAL_MODEL_MODE,
    AgentEvaluationReport,
    AgentSuiteRunner,
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the interview_agent_v1 suite")
    parser.add_argument(
        "--mode",
        choices=[CONTRACT_MODE, REAL_MODEL_MODE],
        default=CONTRACT_MODE,
        help="contract = scripted model (no API key); real_model = production LLM",
    )
    parser.add_argument(
        "--suite-version",
        default="interview_agent_v1",
        help="frozen suite version under backend/tests/fixtures/agent_eval/",
    )
    parser.add_argument(
        "--tasks",
        default="",
        help="逗号分隔的 task id 子集（默认全部 24 个）",
    )
    parser.add_argument("--candidate-label", default="candidate")
    parser.add_argument("--database-url", default="", help="默认使用临时 SQLite")
    parser.add_argument("--output", default="", help="报告 JSON 输出路径")
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=0,
        help="只跑前 N 个任务（real_model 的成本控制）",
    )
    parser.add_argument(
        "--fail-on-hard-gate",
        action="store_true",
        help="hard gate 违规时以退出码 1 结束（CI 使用）",
    )
    return parser


def _resolve_database_url(explicit: str) -> tuple[str, str]:
    """返回 (database_url, temp_dir)；未显式指定时用临时 SQLite。"""
    if explicit:
        return explicit, ""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="agent-eval-"))
    return f"sqlite+aiosqlite:///{tmp / 'eval.db'}", str(tmp)


def _build_real_model_gateway() -> Any:
    """复用生产 gateway（含 telemetry 与 pricing），不另建第二套模型层。"""
    from app.bootstrap import ApplicationContainer
    from app.core.config import get_settings

    container = ApplicationContainer(get_settings())
    gateway = container.llm
    if not getattr(gateway, "configured", False):
        raise SystemExit(
            "real_model mode requires a configured LLM gateway "
            "(set LLM_API_KEY); refusing to fabricate results."
        )
    return gateway


def _render_markdown(report: AgentEvaluationReport, model_label: str) -> str:
    metrics = report.metrics
    lines = [
        "# Agent Evaluation Report",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| suite_version | `{report.suite_version}` |",
        f"| mode | `{report.mode}` |",
        f"| candidate | `{report.candidate_label}` |",
        f"| model | `{model_label}` |",
        f"| git SHA | `{report.git_sha}` |",
        f"| sample size | {report.sample_size} |",
        f"| started | {report.started_at} |",
        f"| finished | {report.finished_at} |",
        f"| hard gates | {'PASS' if report.passed else 'BLOCK'} |",
        "",
        "## 核心指标",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for key in sorted(metrics):
        lines.append(f"| `{key}` | {metrics[key]} |")
    if report.hard_gate_violations:
        lines += ["", "## Hard gate violations", ""]
        for item in report.hard_gate_violations:
            lines.append(f"- `{item.get('metric')}` = {item.get('value')} (limit {item.get('limit') or item.get('required')})")
    failures = [r for r in report.task_results if not r.succeeded]
    if failures:
        lines += ["", "## 失败任务", ""]
        for result in failures:
            lines.append(
                f"- `{result.task_id}` [{result.category}] status={result.status} "
                f"category={list(result.failure_categories)}"
            )
    if report.limitations:
        lines += ["", "## 已知限制", ""]
        lines += [f"- {item}" for item in report.limitations]
    lines.append("")
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> int:
    from app.infrastructure.database import Database

    suite = load_suite(suite_version=args.suite_version)
    database_url, _tmp = _resolve_database_url(args.database_url)
    database = Database(database_url)
    await database.create_schema()

    gateway = None
    model_label = "contract-scripted-model"
    if args.mode == REAL_MODEL_MODE:
        gateway = _build_real_model_gateway()
        model_label = getattr(gateway, "model_for", lambda _route: "unknown")(
            "fast"
        ) if hasattr(gateway, "model_for") else "production-gateway"

    try:
        runner = AgentSuiteRunner(
            suite=suite,
            database=database,
            mode=args.mode,
            gateway=gateway,
            candidate_label=args.candidate_label,
        )
        task_ids = [item for item in args.tasks.split(",") if item.strip()] or None
        if task_ids and args.max_tasks:
            task_ids = task_ids[: args.max_tasks]
        elif args.max_tasks:
            task_ids = [task.id for task in suite.tasks][: args.max_tasks]
        report = await runner.run(
            task_ids=task_ids,
            candidate_version=args.candidate_label,
        )
    finally:
        await database.dispose()

    payload = report.to_dict()
    payload["model"] = model_label
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    markdown = _render_markdown(report, model_label)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        out.with_suffix(".md").write_text(markdown, encoding="utf-8")
        print(f"wrote {out} and {out.with_suffix('.md')}")
    else:
        print(rendered)

    if args.fail_on_hard_gate and not report.passed:
        print("hard gate violated", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    args = _build_arg_parser().parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
