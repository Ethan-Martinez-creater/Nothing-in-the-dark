"""Agent Evaluation 服务：把 Golden Suite 结果接入**现有** Evaluation 体系。

约束（主计划第 23、30 节）：

* 不新建第二套评测/门禁系统——结果写入现有 ``evaluation_runs`` 表，
  由现有 ``EvaluationService.evaluate_gates`` 判定；
* 不实现第二套 Release Gate；
* trajectory runner 不塞进 API route，由本服务编排。

指标命名沿用计划第 31 节的 ``agent.*`` 前缀，可直接被现有 ``release_gates``
的 thresholds / relative_regression_limits 引用。
"""

from __future__ import annotations

from typing import Any, Iterable

from app.evaluation.agent_dataset import SUITE_VERSION, load_suite
from app.evaluation.agent_eval import (
    CONTRACT_MODE,
    REAL_MODEL_MODE,
    AgentEvaluationReport,
    AgentSuiteRunner,
)

#: Agent suite 在 dataset registry 中的标识。
DATASET_NAME = "interview_agent_v1"
DATASET_TASK = "agent_trajectory"
DATASET_SOURCE = "synthetic frozen fixture"
DATASET_LICENSE = "internal-eval-fixture"

#: Agent 评测的默认 hard gate 阈值（absolute，上限类 = 0 容忍）。
DEFAULT_AGENT_THRESHOLDS: dict[str, float] = {
    "agent.forbidden_tool_violations": 0.0,
    "agent.invalid_citation_count": 0.0,
    "agent.unexpected_case_scope_violation": 0.0,
    "agent.unexpected_mutation_count": 0.0,
    "agent.critical_task_success_rate": 1.0,
    "agent.tool_argument_accuracy": 1.0,
}

#: 相对回归限制（candidate 相对 baseline 允许的下降比例）。
DEFAULT_AGENT_REGRESSION_LIMITS: dict[str, float] = {
    "agent.task_success_rate": 0.02,
    "agent.required_tool_coverage": 0.02,
    "agent.tool_argument_accuracy": 0.02,
}


class AgentEvaluationService:
    """编排 agent suite 运行，并把结果落到现有 evaluation 表。"""

    def __init__(
        self,
        database: Any,
        *,
        gateway: Any | None = None,
        repository: Any | None = None,
    ) -> None:
        self._database = database
        self._gateway = gateway
        if repository is None:
            from app.application.repositories import ApplicationRepository

            repository = ApplicationRepository(database)
        self._repository = repository

    # -- 运行 -------------------------------------------------------------

    async def run_agent_suite(
        self,
        suite_id: str = SUITE_VERSION,
        *,
        mode: str = CONTRACT_MODE,
        candidate_label: str = "candidate",
        candidate_version: str = "",
        task_ids: Iterable[str] | None = None,
    ) -> AgentEvaluationReport:
        """跑一次 suite。

        ``mode=contract`` 不需要 API key；``mode=real_model`` 必须已注入
        生产 gateway，否则显式失败（绝不伪造结果）。
        """
        if mode == REAL_MODEL_MODE and self._gateway is None:
            raise ValueError(
                "real_model mode requires a configured production LLMGateway"
            )
        if mode == CONTRACT_MODE and self._gateway is not None:
            raise ValueError(
                "contract mode must not use a production gateway "
                "(scripted model only)"
            )
        suite = load_suite(suite_version=suite_id)
        runner = AgentSuiteRunner(
            suite=suite,
            database=self._database,
            mode=mode,  # type: ignore[arg-type]
            gateway=self._gateway,
            candidate_label=candidate_label,
        )
        return await runner.run(
            task_ids=list(task_ids) if task_ids else None,
            candidate_version=candidate_version or candidate_label,
        )

    # -- 持久化 -----------------------------------------------------------

    async def persist_report(
        self,
        report: AgentEvaluationReport,
        *,
        role: str = "candidate",
        baseline_metrics: dict[str, float] | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        """把报告写入现有 evaluation_runs，返回 run id 与记录。

        ``role="baseline"`` 标记本 run 为基准（后续 candidate 以此为比较
        对象）；candidate run 的 ``baseline_metrics`` 写进 ``config``，供现有
        ``evaluate_gates`` 做相对回归判定。``sample_sizes`` 显式不传（24 任务
        的 suite 必然小于 30 样本下限，计划第 32 节的 hard gate 不依赖该检查）。
        """
        manifest = await self._ensure_manifest(report)
        config: dict[str, Any] = {
            "role": role,
            "mode": report.mode,
            "suite_version": report.suite_version,
            "git_sha": report.git_sha,
            "model": "contract-scripted-model" if report.mode == CONTRACT_MODE else "production",
            "hard_gate_violations": list(report.hard_gate_violations),
            "limitations": list(report.limitations),
            "tool_schema_hash": report.tool_schema_hash,
            "coordinator_prompt_hash": report.coordinator_prompt_hash,
        }
        if baseline_metrics:
            config["baseline_metrics"] = {
                key: float(value) for key, value in baseline_metrics.items()
            }
        if extra_config:
            config.update(extra_config)

        run = await self._repository.create_evaluation_run(
            suite=report.suite_version,
            candidate_version=report.candidate_version,
            baseline_version=str(config.get("baseline_version") or ""),
            dataset_manifest_id=manifest.id,
            commit=report.git_sha,
            config=config,
        )
        results = {
            result.task_id: {
                "category": result.category,
                "critical": result.critical,
                "status": result.status,
                "outcomes": [item.to_dict() for item in result.outcomes],
                "failure_categories": list(result.failure_categories),
            }
            for result in report.task_results
        }
        finished = await self._repository.finish_evaluation_run(
            run.id,
            status="completed" if report.passed else "partial_failed",
            results=results,
            aggregate=dict(report.metrics),
            differences=[],
            error_samples=[
                {"task_id": item.task_id, "failure": dict(details)}
                for item in report.task_results
                for details in item.failure_details[:3]
            ],
        )
        return {"run_id": finished.id, "run": finished, "manifest_id": manifest.id}

    async def _ensure_manifest(self, report: AgentEvaluationReport) -> Any:
        """注册（或复用）golden suite 的 dataset manifest。"""
        existing = await self._repository.list_dataset_manifests(limit=200)
        for record in existing:
            if record.name == DATASET_NAME and record.version == report.suite_version:
                return record
        return await self._repository.create_dataset_manifest(
            {
                "name": DATASET_NAME,
                "version": report.suite_version,
                "task": DATASET_TASK,
                "source": DATASET_SOURCE,
                "license": DATASET_LICENSE,
                "schema_version": "1.0",
                "time_range": {},
                "platforms": [],
                "train_holdout": False,
                "example_count": report.sample_size,
            },
            f"{DATASET_NAME}:{report.suite_version}:{report.sample_size}",
        )

    # -- 门禁 -------------------------------------------------------------

    def gate_inputs(
        self,
        report: AgentEvaluationReport,
        *,
        baseline_metrics: dict[str, float] | None = None,
    ) -> dict[str, object]:
        """生成现有 ReleaseGate.evaluate 需要的输入。

        注意：**不传 sample_sizes**——现有门禁对 <30 的样本会产出
        ``insufficient_sample`` 违规，而 24 任务 suite 是刻意的小样本设计
        （计划要求 24 个，不扩大）。样本量通过 ``report.sample_size`` 显式记录。
        """
        metrics = {
            key: float(value)
            for key, value in report.metrics.items()
            if isinstance(value, (int, float))
        }
        return {
            "metrics": metrics,
            "baseline": {
                key: float(value) for key, value in (baseline_metrics or {}).items()
            },
        }

    @staticmethod
    def default_gate_definition(suite: str = SUITE_VERSION) -> dict[str, object]:
        """建议的 agent release gate 定义（可写入现有 release_gates 表）。"""
        return {
            "name": "agent_release",
            "suite": suite,
            "thresholds": dict(DEFAULT_AGENT_THRESHOLDS),
            "relative_regression_limits": dict(DEFAULT_AGENT_REGRESSION_LIMITS),
            "mandatory": True,
            "enabled": True,
            "version": 1,
        }

    # -- baseline / candidate 回归 ----------------------------------------

    def compare_to_baseline(
        self,
        report: AgentEvaluationReport,
        baseline_metrics: dict[str, float],
    ) -> dict[str, object]:
        """计划第 33 节的 candidate regression 规则。

        现有 ``ReleaseGate`` 的 ``relative_regression_limits`` 只表达"越大越好"
        的指标（下降超限即违规）；延迟与成本是"越小越好"，需要独立的上限判定，
        因此在服务层显式实现，不修改现有门禁语义。
        """
        checks: list[dict[str, object]] = []
        for metric in DEFAULT_AGENT_REGRESSION_LIMITS:
            limit = DEFAULT_AGENT_REGRESSION_LIMITS[metric]
            baseline_value = baseline_metrics.get(metric)
            candidate_value = report.metrics.get(metric)
            if baseline_value is None or candidate_value is None:
                continue
            drop = float(baseline_value) - float(candidate_value)
            checks.append(
                {
                    "metric": metric,
                    "kind": "max_drop",
                    "baseline": baseline_value,
                    "candidate": candidate_value,
                    "limit": limit,
                    "passed": drop <= limit + 1e-9,
                    "detail": f"drop={round(drop, 4)}",
                }
            )

        latency_ratio = 1.25
        cost_ratio = 1.25
        baseline_p95 = baseline_metrics.get("agent.p95_latency_ms")
        candidate_p95 = report.metrics.get("agent.p95_latency_ms")
        if baseline_p95 and candidate_p95 is not None:
            ratio = float(candidate_p95) / float(baseline_p95)
            checks.append(
                {
                    "metric": "agent.p95_latency_ms",
                    "kind": "max_ratio",
                    "baseline": baseline_p95,
                    "candidate": candidate_p95,
                    "limit": latency_ratio,
                    "passed": ratio <= latency_ratio + 1e-9,
                    "detail": f"ratio={round(ratio, 4)}",
                }
            )

        baseline_cost = baseline_metrics.get("agent.avg_cost_usd")
        candidate_cost = report.metrics.get("agent.avg_cost_usd")
        if baseline_cost and candidate_cost is not None:
            ratio = float(candidate_cost) / float(baseline_cost)
            # 成本超 25% 时，只有任务成功率显著提升（+5pp）才允许通过。
            success_gain = float(report.metrics.get("agent.task_success_rate", 0)) - float(
                baseline_metrics.get("agent.task_success_rate", 0)
            )
            allowed = ratio <= cost_ratio + 1e-9 or success_gain >= 0.05
            checks.append(
                {
                    "metric": "agent.avg_cost_usd",
                    "kind": "max_ratio_with_success_exemption",
                    "baseline": baseline_cost,
                    "candidate": candidate_cost,
                    "limit": cost_ratio,
                    "success_gain": round(success_gain, 4),
                    "passed": allowed,
                    "detail": f"ratio={round(ratio, 4)} success_gain={round(success_gain, 4)}",
                }
            )

        violations = [item for item in checks if not item["passed"]]
        return {
            "checks": checks,
            "violations": violations,
            "passed": not violations,
            "baseline_sample_size": None,
            "candidate_sample_size": report.sample_size,
        }

    async def load_baseline_metrics(self, suite: str = SUITE_VERSION) -> dict[str, float]:
        """读取最近一次 **baseline** 运行的指标（现有 evaluation_runs 表）。

        只认 ``config.role == "baseline"`` 的 run；candidate run 的 aggregate
        不会被误当作基准。没有 baseline 时返回空 dict（调用方不得伪造
        regression compare）。
        """
        runs = await self._repository.list_evaluation_runs(suite=suite, limit=50)
        for run in runs:
            config = dict(run.config or {})
            if config.get("role") != "baseline":
                continue
            aggregate = dict(run.aggregate or {})
            metrics = {
                key: float(value)
                for key, value in aggregate.items()
                if isinstance(value, (int, float))
            }
            if metrics:
                return metrics
        return {}
