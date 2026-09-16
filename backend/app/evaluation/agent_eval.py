"""Agent Eval Runner —— 让**真实 production Agent Runtime** 跑 Golden Tasks。

分层（计划第 19–24 节）：

* **Tier A（contract）**：scripted model + 真实 Coordinator / Tool Registry /
  Permission / Approval / Sandbox / Run persistence，验证编排合同；
* **Tier B（real_model）**：真实 model gateway + 同一份期望，验证 agent 质量；
* Tier C（live smoke）不在这里，也永不进入 release gate。

Runner 不实现第二套 runtime：它构造的 ``GraphWorker`` / ``AgentRunService`` /
``ToolRegistry`` 全部是生产组件，只是把 LLMGateway 换成了 scripted 实现。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import subprocess
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Literal, Sequence

from app.evaluation.agent_dataset import (
    SUITE_VERSION,
    AgentGoldenSuite,
    AgentGoldenTask,
)
from app.evaluation.agent_evaluators import (
    EvalContext,
    EvaluationOutcome,
    failure_categories,
    iter_failure_details,
    run_deterministic_evaluators,
)
from app.evaluation.agent_fixture_seed import (
    EvalDataStack,
    SeededFixture,
    build_stack,
    seed_fixture,
)
from app.evaluation.agent_trace import (
    AgentTrace,
    CaseStateSnapshot,
    capture_state,
    collect_trace,
    diff_snapshots,
    review_item_views,
)

EvalMode = Literal["contract", "real_model"]
CONTRACT_MODE: EvalMode = "contract"
REAL_MODEL_MODE: EvalMode = "real_model"

#: 单任务等待终态的上限（防止卡死；contract 模式应远小于此值）。
TASK_TIMEOUT_SECONDS = 240

#: 关键工具的“有意义参数”覆盖表；其余工具的参数由 input schema 自动推导。
_TOOL_ARG_OVERRIDES: dict[str, dict[str, Any]] = {
    "query_social_posts": {"limit": 20},
    "query_social_comments": {"limit": 20},
    "query_claims": {"limit": 20},
    "query_evidence": {"limit": 20},
    "query_findings": {"limit": 20},
    "query_reports": {"limit": 20},
    "query_review_items": {"limit": 20},
    "aggregate_social_data": {"group_by": "platform"},
    "classify_sentiment": {"limit": 20},
    "start_social_collection": {"platforms": ["weibo"], "limit": 20},
    "collect_social_posts": {"platforms": ["weibo"], "limit": 20},
    "build_report": {"title": "调查阶段报告"},
}


# ---------------------------------------------------------------------------
# Scripted model（Tier A 的“模型”）
# ---------------------------------------------------------------------------


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 - 无 git 环境时降级
        return "unknown"


def _prompt_hash(task: AgentGoldenTask) -> str:
    payload = {
        "user_prompt": task.user_prompt,
        "expected": task.expected.to_dict(),
        "budgets": task.budgets.to_dict(),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_tool_arguments(
    tool_name: str,
    input_model: Any,
    *,
    case_id: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按工具 input schema 推导一份合法参数（case_id 由 runtime 注入，不填）。

    ``overrides`` 来自任务的 ``expected_tool_arguments``，优先于默认模板
    （例如 dispatch_expert 必须声明委派给哪个专家）。
    """
    arguments: dict[str, Any] = dict(_TOOL_ARG_OVERRIDES.get(tool_name, {}))
    if overrides:
        arguments.update(overrides)
    if input_model is None:
        return arguments
    schema = input_model.model_json_schema()
    properties: dict[str, Any] = schema.get("properties", {}) or {}
    required: Sequence[str] = schema.get("required", []) or []
    for field_name in required:
        if field_name in arguments or field_name == "case_id":
            continue
        field_schema = properties.get(field_name, {}) or {}
        arguments[field_name] = _default_for_schema(field_schema)
    return arguments


def _default_for_schema(field_schema: dict[str, Any]) -> Any:
    if "default" in field_schema:
        return field_schema["default"]
    enum_values = field_schema.get("enum")
    if enum_values:
        return enum_values[0]
    any_of = field_schema.get("anyOf")
    if any_of:
        for candidate in any_of:
            if candidate.get("type") not in (None, "null"):
                return _default_for_schema(candidate)
    field_type = field_schema.get("type")
    if field_type == "string":
        return "eval"
    if field_type == "integer":
        return 1
    if field_type == "number":
        return 1.0
    if field_type == "boolean":
        return False
    if field_type == "array":
        return []
    if field_type == "object":
        return {}
    return None


def build_final_answer(task: AgentGoldenTask, seeded: SeededFixture) -> str:
    """构造 Tier A 的最终回答：覆盖必须包含的内容，并带上期望引用 id。"""
    lines: list[str] = [f"已完成「{task.title}」。"]
    if task.expected.answer_must_contain:
        lines.append("关键事实：" + "、".join(task.expected.answer_must_contain))
    for ref in task.expected.expected_citation_refs:
        runtime_id = seeded.ref_map.get(f"evidence:{ref}")
        if runtime_id:
            lines.append(f"依据证据 {runtime_id}。")
    for kind in task.expected.required_artifact_types:
        lines.append(f"已产出 {kind}。")
    if task.expected.requires_human_escalation:
        lines.append("该操作已提交人工审批，等待人工决策。")
    if task.category == "G6":
        lines.append("说明：现有数据不足的部分已明确标注，未作外推。")
    text = "\n".join(lines)
    # 反向约束：不得出现被禁止的措辞（contract 模式下由脚本保证）。
    for forbidden in task.expected.answer_must_not_contain:
        text = text.replace(forbidden, "（不适用）")
    return text


class ContractGateway:
    """Tier A 的 scripted model：按顺序返回预设的工具调用与最终回答。

    只实现生产 ``LLMGateway`` 接口，不改变 runtime 的任何行为。
    ``steps`` 的每一项要么是 ``(tool_name, arguments)``，要么是最终回答字符串。
    """

    def __init__(
        self,
        steps: list[Any],
        *,
        model_name: str = "contract-scripted-model",
        expert_response: str | None = None,
    ) -> None:
        self.steps = list(steps)
        self.model_name = model_name
        self._index = 0
        #: 专家子 run 的固定响应。子 run 与主 run 共用同一个 gateway 实例，
        #: 若不做角色隔离，专家的模型调用会吃掉主 run 的脚本步骤。
        self.expert_response = expert_response or json.dumps(
            {"summary": "专家分析完成", "conclusions": [], "is_demo": True},
            ensure_ascii=False,
        )

    @property
    def configured(self) -> bool:
        return True

    @staticmethod
    def _is_coordinator(messages: list[Any]) -> bool:
        for message in messages:
            if getattr(message, "role", "") == "system":
                content = getattr(message, "content", "") or ""
                if "协调" in content or "coordinator" in content.lower():
                    return True
        return False

    async def complete(
        self,
        *,
        messages: list[Any],
        tools: list[dict[str, Any]],
        route: Any = None,
        temperature: float = 0,
    ) -> Any:
        from uuid import uuid4

        from app.infrastructure.llm import LLMMessage, LLMResponse, ToolCall

        if not self._is_coordinator(messages):
            # 专家子 run：返回结构化结论，让 worker 物化 expert artifact。
            return LLMResponse(
                message=LLMMessage(role="assistant", content=self.expert_response),
                model=f"{self.model_name}-expert",
            )
        if self._index >= len(self.steps):
            return LLMResponse(
                message=LLMMessage(role="assistant", content='{"done": true}'),
                model=self.model_name,
            )
        step = self.steps[self._index]
        self._index += 1
        if isinstance(step, str):
            return LLMResponse(
                message=LLMMessage(role="assistant", content=step),
                model=self.model_name,
            )
        name, arguments = step
        return LLMResponse(
            message=LLMMessage(role="assistant"),
            tool_calls=[
                ToolCall(
                    id=f"call-{uuid4().hex[:12]}",
                    name=name,
                    arguments=arguments,
                )
            ],
            model=self.model_name,
        )


def build_contract_steps(
    task: AgentGoldenTask,
    seeded: SeededFixture,
    tool_specs: dict[str, Any],
) -> list[Any]:
    """把“期望行为”翻译成 scripted model 的执行步骤。"""
    steps: list[Any] = []
    for tool_name in task.expected.required_tools:
        spec = tool_specs.get(tool_name)
        arguments = build_tool_arguments(
            tool_name,
            getattr(spec, "input_model", None),
            case_id=seeded.case_id(task.case_id or ""),
            overrides=task.expected.expected_tool_arguments.get(tool_name),
        )
        steps.append((tool_name, arguments))
    steps.append(build_final_answer(task, seeded))
    return steps


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: str
    category: str
    critical: bool
    mode: str
    status: str
    run_id: str
    case_id: str
    outcomes: tuple[EvaluationOutcome, ...] = ()
    failure_categories: tuple[str, ...] = ()
    failure_details: tuple[dict[str, object], ...] = ()
    trace_bundle: dict[str, object] = field(default_factory=dict)
    latency_ms: int = 0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """任务是否达成预期：没有 evaluator 失败，且处于可接受的终态。

        ``waiting_approval`` 对"期望人工审批"的任务是成功停等（E1/E8 已判过），
        因此这里不再要求 status 必须是 completed。
        """
        return not self.failure_details and self.status in {
            "completed",
            "waiting_approval",
        }

    def metric(self, name: str) -> float | None:
        for outcome in self.outcomes:
            if outcome.metric == name:
                return outcome.value
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "category": self.category,
            "critical": self.critical,
            "mode": self.mode,
            "status": self.status,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "outcomes": [item.to_dict() for item in self.outcomes],
            "failure_categories": list(self.failure_categories),
            "failure_details": list(self.failure_details),
            "metrics": {
                "input_tokens": self.trace_bundle.get("metrics", {}).get("input_tokens"),
                "output_tokens": self.trace_bundle.get("metrics", {}).get("output_tokens"),
                "estimated_cost": self.trace_bundle.get("metrics", {}).get("estimated_cost"),
                "tool_calls": self.trace_bundle.get("metrics", {}).get("tool_call_count"),
            },
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class AgentEvaluationReport:
    """一次 suite 运行的完整结果（计划第 31 / 55–57 节要求全部上下文）。"""

    suite_version: str
    mode: str
    candidate_label: str
    candidate_version: str
    git_sha: str
    started_at: str
    finished_at: str
    sample_size: int
    metrics: dict[str, float]
    task_results: tuple[TaskResult, ...]
    hard_gate_violations: tuple[dict[str, object], ...] = ()
    limitations: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.hard_gate_violations

    def to_dict(self) -> dict[str, object]:
        return {
            "suite_version": self.suite_version,
            "mode": self.mode,
            "candidate_label": self.candidate_label,
            "candidate_version": self.candidate_version,
            "git_sha": self.git_sha,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "sample_size": self.sample_size,
            "metrics": self.metrics,
            "hard_gate_violations": list(self.hard_gate_violations),
            "task_results": [item.to_dict() for item in self.task_results],
            "limitations": list(self.limitations),
        }


# ---------------------------------------------------------------------------
# 指标聚合
# ---------------------------------------------------------------------------

_RATE_METRICS = (
    "agent.task_success_rate",
    "agent.required_tool_coverage",
    "agent.tool_argument_accuracy",
    "agent.human_escalation_accuracy",
    "agent.evidence_grounding",
)
_COUNT_METRICS = (
    "agent.forbidden_tool_violations",
    "agent.invalid_citation_count",
    "agent.unexpected_mutation_count",
    "agent.unexpected_case_scope_violation",
)


def aggregate_metrics(results: Sequence[TaskResult]) -> dict[str, float]:
    """把逐任务结果聚合为 suite 级指标（rate 取均值，count 取求和）。"""
    metrics: dict[str, float] = {}
    total = len(results)
    if total == 0:
        return metrics

    def values(name: str) -> list[float]:
        return [
            value
            for value in (result.metric(name) for result in results)
            if value is not None
        ]

    # task_success 来自 E1 的 agent.task_success（0/1）
    success = values("agent.task_success")
    metrics["agent.task_success_rate"] = round(sum(success) / total, 4) if success else 0.0
    metrics["agent.tasks_total"] = float(total)
    metrics["agent.tasks_completed"] = float(
        sum(1 for result in results if result.status == "completed")
    )
    for name in _RATE_METRICS[1:]:
        items = values(name)
        if items:
            metrics[name] = round(sum(items) / len(items), 4)
    for name in _COUNT_METRICS:
        items = values(name)
        if items:
            metrics[name] = float(sum(items))

    steps = [
        float(outcome.details.get("agent_steps", 0))
        for result in results
        for outcome in result.outcomes
        if outcome.evaluator == "E9_efficiency"
    ]
    tool_calls = [
        float(outcome.details.get("tool_calls", 0))
        for result in results
        for outcome in result.outcomes
        if outcome.evaluator == "E9_efficiency"
    ]
    latencies = [
        float(outcome.details.get("latency_ms", 0))
        for result in results
        for outcome in result.outcomes
        if outcome.evaluator == "E9_efficiency"
    ]
    input_tokens = [
        float(outcome.details.get("input_tokens", 0))
        for result in results
        for outcome in result.outcomes
        if outcome.evaluator == "E9_efficiency"
    ]
    output_tokens = [
        float(outcome.details.get("output_tokens", 0))
        for result in results
        for outcome in result.outcomes
        if outcome.evaluator == "E9_efficiency"
    ]
    costs = [
        float(outcome.details.get("estimated_cost", 0))
        for result in results
        for outcome in result.outcomes
        if outcome.evaluator == "E9_efficiency"
    ]
    if steps:
        metrics["agent.avg_steps"] = round(sum(steps) / len(steps), 2)
    if tool_calls:
        metrics["agent.avg_tool_calls"] = round(sum(tool_calls) / len(tool_calls), 2)
    if latencies:
        ordered = sorted(latencies)
        metrics["agent.p50_latency_ms"] = round(_percentile(ordered, 50), 1)
        metrics["agent.p95_latency_ms"] = round(_percentile(ordered, 95), 1)
    if input_tokens:
        metrics["agent.avg_input_tokens"] = round(sum(input_tokens) / len(input_tokens), 1)
    if output_tokens:
        metrics["agent.avg_output_tokens"] = round(sum(output_tokens) / len(output_tokens), 1)
    if costs:
        metrics["agent.avg_cost_usd"] = round(sum(costs) / len(costs), 6)

    critical = [result for result in results if result.critical]
    if critical:
        critical_success = [
            value
            for value in (result.metric("agent.task_success") for result in critical)
            if value is not None
        ]
        metrics["agent.critical_task_success_rate"] = (
            round(sum(critical_success) / len(critical), 4) if critical_success else 0.0
        )
    return metrics


def _percentile(ordered: Sequence[float], percentile: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * (percentile / 100.0)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def evaluate_hard_gates(report: AgentEvaluationReport) -> list[dict[str, object]]:
    """计划第 32 节的固定 hard gates。"""
    violations: list[dict[str, object]] = []
    checks = {
        "agent.forbidden_tool_violations": 0.0,
        "agent.invalid_citation_count": 0.0,
        "agent.unexpected_case_scope_violation": 0.0,
        "agent.unexpected_mutation_count": 0.0,
    }
    for metric, limit in checks.items():
        value = report.metrics.get(metric)
        if value is None:
            continue
        if value > limit:
            violations.append({"metric": metric, "value": value, "limit": limit})
    critical_rate = report.metrics.get("agent.critical_task_success_rate")
    if critical_rate is not None and critical_rate < 1.0:
        violations.append(
            {
                "metric": "agent.critical_task_success_rate",
                "value": critical_rate,
                "required": 1.0,
            }
        )
    return violations


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class AgentSuiteRunner:
    """对 Golden Suite 逐任务执行真实 runtime 并评测。"""

    def __init__(
        self,
        *,
        suite: AgentGoldenSuite,
        database: Any,
        mode: EvalMode = CONTRACT_MODE,
        gateway: Any | None = None,
        candidate_label: str = "candidate",
        task_timeout_seconds: int = TASK_TIMEOUT_SECONDS,
    ) -> None:
        self._suite = suite
        self._database = database
        self._mode = mode
        self._gateway = gateway
        self._candidate_label = candidate_label
        self._task_timeout = task_timeout_seconds
        self._stack: EvalDataStack | None = None

    async def _build(self) -> EvalDataStack:
        stack = build_stack(self._database)
        self._stack = stack
        return stack

    def _build_tools(self, stack: EvalDataStack, gateway: Any) -> Any:
        """构造真实 Tool Registry（生产 build_tool_registry，非 eval 专用）。"""
        from app.harness.database_tools import register_database_tools
        from app.harness.intelligence_tools import register_intelligence_tools
        from app.harness.skills import SkillRegistry
        from app.harness.tool_factory import build_tool_registry
        from app.infrastructure.crawler.demo import DemoCrawlerAdapter
        from app.infrastructure.embeddings import EmbeddingWorkerClient

        registry = build_tool_registry(
            DemoCrawlerAdapter(),
            SkillRegistry(),
            stack.knowledge,
            EmbeddingWorkerClient("http://localhost:1", dimensions=1024, timeout_seconds=1),
            stack.social,
            stack.repository,
            llm=gateway,
        )
        register_database_tools(registry, None)
        register_intelligence_tools(registry, None)
        return registry

    def _build_worker(self, stack: EvalDataStack, gateway: Any, tools: Any, task: AgentGoldenTask) -> Any:
        from langgraph.checkpoint.memory import MemorySaver

        from app.application.graph_worker import GraphWorker
        from app.harness.skills import SkillRegistry

        return GraphWorker(
            stack.repository,
            gateway,
            tools,
            SkillRegistry(),
            worker_id=f"eval-{task.id}",
            poll_interval_seconds=0.05,
            lease_seconds=60,
            max_turns=task.budgets.max_agent_steps,
            max_tool_calls=task.budgets.max_tool_calls,
            max_cost=1.0,
            checkpointer=MemorySaver(),
            social=stack.social,
        )

    async def run(
        self,
        *,
        task_ids: Iterable[str] | None = None,
        candidate_version: str = "",
    ) -> AgentEvaluationReport:
        suite = self._suite
        selected = (
            [suite.by_id(task_id) for task_id in task_ids]
            if task_ids is not None
            else list(suite.tasks)
        )
        stack = await self._build()
        started = datetime.now(timezone.utc)
        results: list[TaskResult] = []
        for task in selected:
            results.append(await self._run_task(stack, task))
        finished = datetime.now(timezone.utc)
        metrics = aggregate_metrics(results)
        report = AgentEvaluationReport(
            suite_version=suite.suite_version,
            mode=self._mode,
            candidate_label=self._candidate_label,
            candidate_version=candidate_version or _git_sha(),
            git_sha=_git_sha(),
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            sample_size=len(results),
            metrics=metrics,
            task_results=tuple(results),
            limitations=(
                (
                    "Tier A 使用 scripted model：最终回答由脚本构造，"
                    "E7 在 Tier A 验证引用链路可解析，grounding 质量需 Tier B 验证。",
                )
                if self._mode == CONTRACT_MODE
                else ()
            ),
        )
        violations = evaluate_hard_gates(report)
        return replace(report, hard_gate_violations=tuple(violations))

    async def _run_task(self, stack: EvalDataStack, task: AgentGoldenTask) -> TaskResult:
        from app.application.agent_service import AgentRunService

        if not task.case_id:
            raise ValueError(f"task {task.id} has no case_id")
        fixture = self._suite.fixtures.get(task.fixture_id)
        if fixture is None:
            raise ValueError(f"task {task.id}: fixture {task.fixture_id} missing")

        seeded = await seed_fixture(
            stack, fixture_id=task.fixture_id, fixture=fixture
        )
        case_id = seeded.case_id(task.case_id)

        gateway = self._gateway
        if gateway is None:
            if self._mode != CONTRACT_MODE:
                raise RuntimeError(
                    "real_model mode requires an explicit gateway (production LLM)"
                )
            tools_for_args = self._build_tools(stack, None)
            specs = {name: tools_for_args.get(name) for name in tools_for_args.names()}
            steps = build_contract_steps(task, seeded, specs)
            gateway = ContractGateway(steps)

        tools = self._build_tools(stack, gateway)
        worker = self._build_worker(stack, gateway, tools, task)
        service = AgentRunService(stack.repository, worker)

        before = await capture_state(
            stack.repository,
            case_id,
            finding_repository=stack.finding_repository,
            social_repository=stack.social,
        )
        started_ms = time.monotonic()
        run = await service.start(
            case_id=case_id,
            content=task.user_prompt,
            # 评测环境不允许隐式批准采集；高风险动作必须走审批中断。
            approve_crawl=False,
        )
        error: str | None = None
        try:
            await self._drive(stack, worker, run.id)
        except Exception as exc:  # noqa: BLE001 - 任务级失败必须记录而非中断整套
            error = f"{type(exc).__name__}: {exc}"[:300]
        elapsed_ms = int((time.monotonic() - started_ms) * 1000)

        after = await capture_state(
            stack.repository,
            case_id,
            finding_repository=stack.finding_repository,
            social_repository=stack.social,
        )
        review_items = review_item_views(
            await stack.repository.list_review_items(case_id, limit=200)
        )
        trace = await collect_trace(
            stack.repository, run.id, finding_repository=stack.finding_repository
        )
        outcomes = run_deterministic_evaluators(
            EvalContext(
                task=task,
                trace=trace,
                state_before=before,
                state_after=after,
                state_diff=diff_snapshots(before, after),
                review_items=review_items,
                known_ids=self._known_ids(seeded),
                ref_map=seeded.ref_map,
                tool_specs=self._tool_specs(tools),
                expected_case_id=case_id,
                wall_clock_ms=elapsed_ms,
            )
        )
        categories = failure_categories(outcomes)
        return TaskResult(
            task_id=task.id,
            category=task.category,
            critical=task.critical,
            mode=self._mode,
            status=trace.status,
            run_id=trace.run_id,
            case_id=case_id,
            outcomes=outcomes,
            failure_categories=tuple(categories),
            failure_details=tuple(iter_failure_details(task, outcomes)),
            trace_bundle=trace.to_bundle(),
            latency_ms=elapsed_ms or trace.total_duration_ms(),
            error=error,
        )

    async def _drive(self, stack: EvalDataStack, worker: Any, run_id: str) -> None:
        """驱动 worker 直到 run 到达终态（含等待审批）。

        必须用后台循环而非 ``tick(wait=True)``：``dispatch_expert`` 会同步等待
        专家子 run（``_wait_for_child``），单次阻塞式 tick 会让子 run 永远无法被
        claim，导致死锁。后台循环可以同时推进主 run 与子 run。
        """
        await worker.start()
        try:
            deadline = time.monotonic() + self._task_timeout
            while time.monotonic() < deadline:
                run = await stack.repository.get_agent_run(run_id)
                if run.status in {
                    "completed",
                    "failed",
                    "cancelled",
                    "waiting_approval",
                }:
                    await self._settle(stack, run_id)
                    return
                await asyncio.sleep(0.1)
            raise TimeoutError(
                f"run {run_id} did not reach a terminal state in time"
            )
        finally:
            await worker.stop()

    async def _settle(self, stack: EvalDataStack, run_id: str) -> None:
        """等 runtime 收尾完成再停 worker。

        runtime 先写终态、随后才 emit ``agent_end`` 并释放 lease；若此时立即
        ``worker.stop()``，正在执行的 task 会被 cancel，触发 CancelledError 分支
        把已完成的 run 误标成 ``cancelled``。lease 释放是收尾完成的可观测信号。
        """
        for _ in range(60):
            run = await stack.repository.get_agent_run(run_id)
            if not run.lease_owner:
                break
            await asyncio.sleep(0.1)
        # 再留一小段静默期覆盖 emit 事件的尾部。
        await asyncio.sleep(0.3)

    @staticmethod
    def _tool_specs(registry: Any) -> dict[str, Any]:
        return {name: registry.get(name) for name in registry.names()}

    @staticmethod
    def _known_ids(seeded: SeededFixture) -> dict[str, set[str]]:
        buckets = seeded.known_ids()
        # ref_map 的 key 形如 "evidence:rv_ev_01"
        for key, value in seeded.ref_map.items():
            category = key.split(":", 1)[0]
            buckets.setdefault(category, set()).add(value)
        return buckets
