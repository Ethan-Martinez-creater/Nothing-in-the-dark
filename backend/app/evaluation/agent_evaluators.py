"""确定性 trajectory evaluators（E1–E10）。

设计原则（来自 Interview Readiness 主计划第 26–29 节）：

* **绝不用 LLM 判断**工具选择、参数正确性、case 越界、mutation、citation 是否存在；
  这些全部由本模块的纯函数判定；
* LLM judge 只允许用于 relevance / completeness / uncertainty calibration，
  且必须记录 judge model 与 prompt 版本（见 ``agent_judge.py``，默认不启用）；
* 每个 evaluator 输出一个 metric + 值 + 证据 details，聚合在 runner 完成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.evaluation.agent_dataset import AgentGoldenTask
from app.evaluation.agent_trace import (
    AgentTrace,
    CaseStateSnapshot,
    ReviewItemView,
    StateDiff,
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

#: 参数中 limit 类字段的上界（防止无界查询）。
MAX_BOUNDED_LIMIT = 200

#: 看起来像内部 id 的 token（uuid4 hex / 带连字符 uuid）。
_ID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\b[0-9a-f]{32}\b"
)


@dataclass(frozen=True, slots=True)
class EvalContext:
    """一次任务评测的全部输入（只读）。"""

    task: AgentGoldenTask
    trace: AgentTrace
    state_before: CaseStateSnapshot
    state_after: CaseStateSnapshot
    state_diff: StateDiff
    review_items: tuple[ReviewItemView, ...] = ()
    #: 类别名 -> 该类别已知 id 集合（evidence / finding / artifact / post / claim / review）
    known_ids: Mapping[str, set[str]] = field(default_factory=dict)
    #: fixture 逻辑 key -> 运行时真实 id
    ref_map: Mapping[str, str] = field(default_factory=dict)
    #: 工具名 -> ToolSpec（用于参数 schema 校验）
    tool_specs: Mapping[str, Any] = field(default_factory=dict)
    expected_case_id: str = ""
    #: 端到端墙钟耗时（runner 测量）；contract 模式下工具 duration 可能为 0。
    wall_clock_ms: int = 0

    def all_known_ids(self) -> set[str]:
        out: set[str] = set()
        for values in self.known_ids.values():
            out |= set(values)
        return out


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    """单个 evaluator 的结果。"""

    evaluator: str
    metric: str
    value: float
    passed: bool | None
    details: dict[str, Any] = field(default_factory=dict)
    failure_category: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "evaluator": self.evaluator,
            "metric": self.metric,
            "value": round(self.value, 4),
            "passed": self.passed,
            "details": self.details,
            "failure_category": self.failure_category,
        }


# ---------------------------------------------------------------------------
# 状态断言求值（E1 的核心输入）
# ---------------------------------------------------------------------------


def unexpected_state_changes(diff: StateDiff) -> dict[str, object]:
    """非预期状态变更（唯一的 mutation 判定口径）。

    只读任务允许工具产出 artifact（build_report / 专家分析都会写 artifact），
    因此 artifact 新增不计入 mutation；finding / review 状态变化与
    posts / claims / evidence 的数量变化都算。

    E5 与 ``no_mutation`` 断言共用本函数，避免两处口径漂移。
    """
    unexpected: dict[str, object] = {}
    if diff.changed_findings:
        unexpected["changed_findings"] = {
            key: {"before": value[0], "after": value[1]}
            for key, value in diff.changed_findings.items()
        }
    if diff.changed_reviews:
        unexpected["changed_reviews"] = {
            key: {"before": value[0], "after": value[1]}
            for key, value in diff.changed_reviews.items()
        }
    mutated_counts = {
        key: value
        for key, value in diff.mutated_counts().items()
        if key != "artifacts"
    }
    if mutated_counts:
        unexpected["count_deltas"] = mutated_counts
    return unexpected


def _assertion_holds(assertion: Any, ctx: EvalContext) -> tuple[bool, str]:
    """返回 (是否满足, 说明)。"""
    kind = assertion.kind
    target = assertion.target
    expected = assertion.expected
    trace = ctx.trace

    if kind == "no_mutation":
        unexpected = unexpected_state_changes(ctx.state_diff)
        return (not unexpected), (
            "" if not unexpected else f"state changed: {unexpected}"
        )
    if kind == "artifact_exists":
        present = any(item.kind == target for item in trace.artifacts)
        return present is bool(expected), "" if present else f"artifact {target} absent"
    if kind == "artifact_absent":
        present = any(item.kind == target for item in trace.artifacts)
        return (not present) is bool(expected), (
            "" if not present else f"artifact {target} unexpectedly present"
        )
    if kind == "finding_exists":
        present = any(item.kind == target for item in trace.findings)
        return present is bool(expected), "" if present else f"finding kind {target} absent"
    if kind == "finding_absent":
        present = any(item.kind == target for item in trace.findings)
        return (not present) is bool(expected), (
            "" if not present else f"finding kind {target} unexpectedly present"
        )
    if kind == "finding_status":
        matched = [item for item in trace.findings if item.kind == target]
        if not matched:
            return False, f"no finding of kind {target}"
        actual = matched[0].status
        return actual == str(expected), (
            "" if actual == str(expected) else f"finding {target} status {actual} != {expected}"
        )
    if kind == "review_item_exists":
        matched = [item for item in ctx.review_items if item.object_type == target]
        present = bool(matched)
        return present is bool(expected), "" if present else f"no review item for {target}"
    if kind == "tool_called":
        called = trace.called(target)
        return called is bool(expected), "" if called else f"tool {target} not called"
    if kind == "tool_not_called":
        called = trace.called(target)
        ok = (not called) is bool(expected)
        return ok, "" if ok else f"tool {target} was called"
    if kind == "tool_call_status":
        statuses = trace.tool_call_statuses(target)
        if not statuses:
            return False, f"tool {target} never called"
        ok = str(expected) in statuses
        return ok, "" if ok else f"tool {target} statuses {list(statuses)} lack {expected}"
    if kind == "run_status":
        ok = trace.status == str(expected)
        return ok, "" if ok else f"run status {trace.status} != {expected}"
    return False, f"unsupported assertion kind {kind}"


def evaluate_state_assertions(ctx: EvalContext) -> tuple[bool, list[dict[str, object]]]:
    failures: list[dict[str, object]] = []
    for assertion in ctx.task.expected.required_state_assertions:
        ok, note = _assertion_holds(assertion, ctx)
        if not ok:
            failures.append(
                {
                    "kind": assertion.kind,
                    "target": assertion.target,
                    "expected": assertion.expected,
                    "note": note,
                }
            )
    return (not failures), failures


# ---------------------------------------------------------------------------
# E1 — Task Completion
# ---------------------------------------------------------------------------


def evaluate_task_completion(ctx: EvalContext) -> EvaluationOutcome:
    """0/1：state assertions 全满足 + required artifacts 存在 + 已给出回答。

    期望人工审批的任务（``requires_human_escalation=True``）停在
    ``waiting_approval`` 属于**成功的停等状态**——此时没有最终回答是正常的，
    不能因此判失败。
    """
    assertions_ok, assertion_failures = evaluate_state_assertions(ctx)
    missing_artifacts = [
        kind
        for kind in ctx.task.expected.required_artifact_types
        if not any(item.kind == kind for item in ctx.trace.artifacts)
    ]
    answered = bool(ctx.trace.final_answer.strip())
    suspended_as_expected = (
        ctx.trace.status == "waiting_approval"
        and ctx.task.expected.requires_human_escalation is True
    )
    completed = (
        assertions_ok
        and not missing_artifacts
        and (answered or suspended_as_expected)
    )

    category: str | None = None
    if not completed:
        if any(item["kind"] in {"no_mutation"} for item in assertion_failures):
            category = "state mutation"
        elif any(item["kind"] == "tool_call_status" for item in assertion_failures):
            category = "routing"
        elif missing_artifacts:
            category = "model reasoning"
        else:
            category = "grounding"

    return EvaluationOutcome(
        evaluator="E1_task_completion",
        metric="agent.task_success",
        value=1.0 if completed else 0.0,
        passed=completed,
        details={
            "assertion_failures": assertion_failures,
            "missing_artifacts": missing_artifacts,
            "answered": answered,
            "run_status": ctx.trace.status,
        },
        failure_category=category,
    )


# ---------------------------------------------------------------------------
# E2 — Required Tool Coverage
# ---------------------------------------------------------------------------


def evaluate_required_tool_coverage(ctx: EvalContext) -> EvaluationOutcome:
    required = list(ctx.task.expected.required_tools)
    if not required:
        return EvaluationOutcome(
            evaluator="E2_required_tool_coverage",
            metric="agent.required_tool_coverage",
            value=1.0,
            passed=True,
            details={"required": [], "missing": []},
        )
    missing = [name for name in required if not ctx.trace.called(name)]
    covered = len(required) - len(missing)
    value = covered / len(required)
    return EvaluationOutcome(
        evaluator="E2_required_tool_coverage",
        metric="agent.required_tool_coverage",
        value=value,
        passed=not missing,
        details={"required": required, "missing": missing, "called": sorted(set(ctx.trace.tool_names))},
        failure_category=None if not missing else "routing",
    )


# ---------------------------------------------------------------------------
# E3 — Forbidden Tool Violations
# ---------------------------------------------------------------------------


def evaluate_forbidden_tools(ctx: EvalContext) -> EvaluationOutcome:
    forbidden = list(ctx.task.expected.forbidden_tools)
    violations = [name for name in forbidden if ctx.trace.called(name)]
    return EvaluationOutcome(
        evaluator="E3_forbidden_tool_violations",
        metric="agent.forbidden_tool_violations",
        value=float(len(violations)),
        passed=not violations,
        details={"forbidden": forbidden, "violations": violations},
        failure_category=None if not violations else "routing",
    )


# ---------------------------------------------------------------------------
# E4 — Tool Argument Correctness
# ---------------------------------------------------------------------------


def _bounded_limit_violations(arguments: Mapping[str, Any]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for key, value in arguments.items():
        if "limit" not in key and "max_" not in key:
            continue
        if isinstance(value, int) and value > MAX_BOUNDED_LIMIT:
            out.append({"argument": key, "value": value, "max": MAX_BOUNDED_LIMIT})
    return out


def evaluate_tool_arguments(ctx: EvalContext) -> EvaluationOutcome:
    """参数 schema / 必填 / 有界性检查（确定性，不用 LLM）。"""
    checked: list[dict[str, object]] = []
    problems: list[dict[str, object]] = []
    for call in ctx.trace.tool_calls:
        entry: dict[str, object] = {"tool": call.tool, "call_id": call.id, "problems": []}
        spec = ctx.tool_specs.get(call.tool)
        if spec is None:
            entry["problems"].append("unknown_tool")  # type: ignore[union-attr]
            problems.append(entry)
            checked.append(entry)
            continue
        input_model = getattr(spec, "input_model", None)
        if input_model is not None:
            try:
                input_model.model_validate(call.arguments)
            except Exception as exc:  # noqa: BLE001 - schema 违规就是评测信号
                entry["problems"].append(f"schema_invalid: {type(exc).__name__}")  # type: ignore[union-attr]
        bounded = _bounded_limit_violations(call.arguments)
        if bounded:
            entry["problems"].extend(  # type: ignore[union-attr]
                f"unbounded_limit:{item['argument']}" for item in bounded
            )
        if entry["problems"]:
            problems.append(entry)
        checked.append(entry)

    total = len(checked)
    value = 1.0 if total == 0 else (total - len(problems)) / total
    return EvaluationOutcome(
        evaluator="E4_tool_argument_correctness",
        metric="agent.tool_argument_accuracy",
        value=value,
        passed=not problems,
        details={"checked_calls": total, "problem_calls": problems},
        failure_category=None if not problems else "argument",
    )


# ---------------------------------------------------------------------------
# E10 — Case Scope Violations
# ---------------------------------------------------------------------------


def evaluate_case_scope(ctx: EvalContext) -> EvaluationOutcome:
    """工具调用参数中的 case_id 必须等于期望 case（越界即 hard gate 违规）。"""
    violations: list[dict[str, object]] = []
    expected = ctx.expected_case_id or ctx.task.case_id or ""
    for call in ctx.trace.tool_calls:
        actual = call.arguments.get("case_id")
        if actual is None:
            continue
        if expected and str(actual) != expected:
            violations.append(
                {"tool": call.tool, "case_id": str(actual), "expected": expected}
            )
    return EvaluationOutcome(
        evaluator="E10_case_scope",
        metric="agent.unexpected_case_scope_violation",
        value=float(len(violations)),
        passed=not violations,
        details={"violations": violations},
        failure_category=None if not violations else "argument",
    )


# ---------------------------------------------------------------------------
# E5 — State Mutation Correctness
# ---------------------------------------------------------------------------


def evaluate_state_mutation(ctx: EvalContext) -> EvaluationOutcome:
    """非预期变更计数。artifact 新增属于工具正常产物，不算违规。"""
    diff = ctx.state_diff
    unexpected = unexpected_state_changes(diff)
    count = (
        len(diff.changed_findings)
        + len(diff.changed_reviews)
        + len(unexpected.get("count_deltas", {}) or {})
    )
    return EvaluationOutcome(
        evaluator="E5_state_mutation",
        metric="agent.unexpected_mutation_count",
        value=float(count),
        passed=count == 0,
        details={
            "unexpected": unexpected,
            "before": ctx.state_before.to_dict(),
            "after": ctx.state_after.to_dict(),
        },
        failure_category=None if count == 0 else "state mutation",
    )


# ---------------------------------------------------------------------------
# E6 — Citation Validity
# ---------------------------------------------------------------------------


def evaluate_citation_validity(ctx: EvalContext) -> EvaluationOutcome:
    """回答中出现的 id token 必须真实存在（幻觉引用计数）。"""
    known = ctx.all_known_ids()
    mentioned = _ID_PATTERN.findall(ctx.trace.final_answer or "")
    unique_mentioned = sorted(set(mentioned))
    invalid = [token for token in unique_mentioned if token not in known]
    return EvaluationOutcome(
        evaluator="E6_citation_validity",
        metric="agent.invalid_citation_count",
        value=float(len(invalid)),
        passed=not invalid,
        details={
            "referenced_ids": unique_mentioned,
            "invalid_ids": invalid,
            "known_id_count": len(known),
        },
        failure_category=None if not invalid else "citation",
    )


# ---------------------------------------------------------------------------
# E7 — Evidence Grounding
# ---------------------------------------------------------------------------


def evaluate_evidence_grounding(ctx: EvalContext) -> EvaluationOutcome:
    """期望证据引用是否真的在回答中被使用（逻辑 key 经 ref_map 解析）。"""
    expected_keys = list(ctx.task.expected.expected_citation_refs)
    if not expected_keys:
        return EvaluationOutcome(
            evaluator="E7_evidence_grounding",
            metric="agent.evidence_grounding",
            value=1.0,
            passed=None,
            details={"applicable": False, "expected_refs": []},
        )
    answer = ctx.trace.final_answer or ""
    resolved: list[dict[str, str]] = []
    for key in expected_keys:
        # ref_map 的 key 带类别前缀（evidence:<logical_ref>）。
        real_id = ctx.ref_map.get(f"evidence:{key}") or ctx.ref_map.get(key, "")
        resolved.append({"logical_ref": key, "runtime_id": real_id})
    hit = [
        item
        for item in resolved
        if item["runtime_id"] and item["runtime_id"] in answer
    ]
    value = len(hit) / len(resolved) if resolved else 1.0
    missing = [item["logical_ref"] for item in resolved if item not in hit]
    return EvaluationOutcome(
        evaluator="E7_evidence_grounding",
        metric="agent.evidence_grounding",
        value=value,
        passed=not missing,
        details={
            "applicable": True,
            "expected_refs": expected_keys,
            "resolved": resolved,
            "grounded": [item["logical_ref"] for item in hit],
            "missing": missing,
        },
        failure_category=None if not missing else "grounding",
    )


# ---------------------------------------------------------------------------
# E8 — Human Escalation Correctness
# ---------------------------------------------------------------------------


def evaluate_human_escalation(ctx: EvalContext) -> EvaluationOutcome:
    """人工升级判定只认**本次运行产生**的升级信号。

    fixture 里预先存在的 review item 不算（否则任何跑在该 case 上的任务都会被
    误判为已升级）；只有本次 run 的 approval、停在 waiting_approval、或本次新增的
    review item 才计入。
    """
    requires = ctx.task.expected.requires_human_escalation
    new_reviews = set(ctx.state_after.review_status) - set(ctx.state_before.review_status)
    escalated = (
        bool(ctx.trace.approvals)
        or ctx.trace.status in {"waiting_approval"}
        or bool(new_reviews)
    )
    if requires is None:
        return EvaluationOutcome(
            evaluator="E8_human_escalation",
            metric="agent.human_escalation_correctness",
            value=1.0,
            passed=None,
            details={"applicable": False, "escalated": escalated},
        )
    ok = escalated is bool(requires)
    return EvaluationOutcome(
        evaluator="E8_human_escalation",
        metric="agent.human_escalation_correctness",
        value=1.0 if ok else 0.0,
        passed=ok,
        details={
            "applicable": True,
            "requires_escalation": bool(requires),
            "escalated": escalated,
            "approvals": [item.to_dict() for item in ctx.trace.approvals],
            "new_review_items": sorted(new_reviews),
            "run_status": ctx.trace.status,
        },
        failure_category=None if ok else ("state mutation" if escalated else "routing"),
    )


# ---------------------------------------------------------------------------
# E9 — Efficiency
# ---------------------------------------------------------------------------


def evaluate_efficiency(ctx: EvalContext) -> EvaluationOutcome:
    """记录成本/延迟指标；budget 超限仅标记，第一版不因成本直接失败。"""
    latency_ms = ctx.wall_clock_ms or ctx.trace.total_duration_ms()
    steps = len(ctx.trace.tool_calls) + 1  # 工具步 + 最终回答步
    budget = ctx.task.budgets
    over_budget: list[str] = []
    if steps > budget.max_agent_steps:
        over_budget.append("max_agent_steps")
    if len(ctx.trace.tool_calls) > budget.max_tool_calls:
        over_budget.append("max_tool_calls")
    if budget.max_latency_ms and latency_ms > budget.max_latency_ms:
        over_budget.append("max_latency_ms")
    if budget.max_input_tokens and ctx.trace.input_tokens > budget.max_input_tokens:
        over_budget.append("max_input_tokens")
    if budget.max_output_tokens and ctx.trace.output_tokens > budget.max_output_tokens:
        over_budget.append("max_output_tokens")
    return EvaluationOutcome(
        evaluator="E9_efficiency",
        metric="agent.efficiency",
        value=float(latency_ms),
        passed=None,
        details={
            "agent_steps": steps,
            "tool_calls": len(ctx.trace.tool_calls),
            "latency_ms": latency_ms,
            "input_tokens": ctx.trace.input_tokens,
            "output_tokens": ctx.trace.output_tokens,
            "estimated_cost": ctx.trace.estimated_cost,
            "budget_exceeded": over_budget,
        },
        failure_category=None if not over_budget else "latency",
    )


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

#: 固定执行的确定性 evaluator 顺序。
DETERMINISTIC_EVALUATORS = (
    ("E1_task_completion", evaluate_task_completion),
    ("E2_required_tool_coverage", evaluate_required_tool_coverage),
    ("E3_forbidden_tool_violations", evaluate_forbidden_tools),
    ("E4_tool_argument_correctness", evaluate_tool_arguments),
    ("E5_state_mutation", evaluate_state_mutation),
    ("E6_citation_validity", evaluate_citation_validity),
    ("E7_evidence_grounding", evaluate_evidence_grounding),
    ("E8_human_escalation", evaluate_human_escalation),
    ("E9_efficiency", evaluate_efficiency),
    ("E10_case_scope", evaluate_case_scope),
)


def run_deterministic_evaluators(ctx: EvalContext) -> tuple[EvaluationOutcome, ...]:
    """执行全部确定性 evaluator；单个失败不影响其它（评测隔离）。"""
    outcomes: list[EvaluationOutcome] = []
    for name, fn in DETERMINISTIC_EVALUATORS:
        try:
            outcomes.append(fn(ctx))
        except Exception as exc:  # noqa: BLE001 - evaluator 崩溃必须显式暴露
            outcomes.append(
                EvaluationOutcome(
                    evaluator=name,
                    metric="agent.evaluator_error",
                    value=0.0,
                    passed=False,
                    details={"error": f"{type(exc).__name__}: {exc}"[:300]},
                    failure_category="model reasoning",
                )
            )
    return tuple(outcomes)


def failure_categories(outcomes: Iterable[EvaluationOutcome]) -> list[str]:
    """从 evaluator 结果收集失败分类（用于 Failure Analysis 报告）。"""
    seen: list[str] = []
    for outcome in outcomes:
        if outcome.passed is False and outcome.failure_category:
            if outcome.failure_category not in seen:
                seen.append(outcome.failure_category)
    return seen


def iter_failure_details(
    task: AgentGoldenTask, outcomes: Sequence[EvaluationOutcome]
) -> list[dict[str, object]]:
    """把失败项整理成报告条目。"""
    items: list[dict[str, object]] = []
    for outcome in outcomes:
        if outcome.passed is not False:
            continue
        items.append(
            {
                "task_id": task.id,
                "evaluator": outcome.evaluator,
                "metric": outcome.metric,
                "value": round(outcome.value, 4),
                "failure_category": outcome.failure_category or "model reasoning",
                "details": outcome.details,
            }
        )
    return items
