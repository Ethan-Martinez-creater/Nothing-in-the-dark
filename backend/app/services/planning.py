"""M17: explicit goals, plan DAGs and completion verification.

Pure, dependency-free planning logic (mirrors the services/evaluation.py
style): state machines, DAG validation, ready-set computation and the
deterministic Completion Verifier.  Persistence and agent dispatch live in
the application layer; this module only decides.

Design rules from the module spec:

* the plan graph must be acyclic; a step whose dependencies are unmet
  cannot run;
* a step reaching 'succeeded' only proves its own output, never the goal;
* a goal reaches 'completed' only when every required acceptance
  criterion is 'satisfied' backed by a CompletionAssessment;
* 'skipped' always carries a policy/human reason; required steps are
  never silently skipped;
* plan changes create a new plan version; evidence maps to the new plan
  without overwriting history.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# State machines (module spec 2 / 3)
# ---------------------------------------------------------------------------

GOAL_DRAFT = "draft"
GOAL_ACTIVE = "active"
GOAL_NEEDS_INPUT = "needs_input"
GOAL_BLOCKED = "blocked"
GOAL_COMPLETED = "completed"
GOAL_FAILED = "failed"
GOAL_CANCELLED = "cancelled"

GOAL_STATES: frozenset[str] = frozenset(
    {
        GOAL_DRAFT,
        GOAL_ACTIVE,
        GOAL_NEEDS_INPUT,
        GOAL_BLOCKED,
        GOAL_COMPLETED,
        GOAL_FAILED,
        GOAL_CANCELLED,
    }
)

STEP_PENDING = "pending"
STEP_READY = "ready"
STEP_RUNNING = "running"
STEP_WAITING_REVIEW = "waiting_review"
STEP_SUCCEEDED = "succeeded"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"
STEP_CANCELLED = "cancelled"

STEP_STATES: frozenset[str] = frozenset(
    {
        STEP_PENDING,
        STEP_READY,
        STEP_RUNNING,
        STEP_WAITING_REVIEW,
        STEP_SUCCEEDED,
        STEP_FAILED,
        STEP_SKIPPED,
        STEP_CANCELLED,
    }
)

#: Steps that may transition into 'succeeded' by their own declaration.
_SELF_DECLARABLE = frozenset({STEP_READY, STEP_RUNNING, STEP_WAITING_REVIEW})

CRITERION_ARTIFACT_EXISTS = "artifact_exists"
CRITERION_SCHEMA_VALID = "schema_valid"
CRITERION_CITATION_COVERAGE = "citation_coverage"
CRITERION_TOOL_SUCCEEDED = "tool_succeeded"
CRITERION_HUMAN_APPROVED = "human_approved"
CRITERION_METRIC_THRESHOLD = "metric_threshold"

CRITERION_TYPES: frozenset[str] = frozenset(
    {
        CRITERION_ARTIFACT_EXISTS,
        CRITERION_SCHEMA_VALID,
        CRITERION_CITATION_COVERAGE,
        CRITERION_TOOL_SUCCEEDED,
        CRITERION_HUMAN_APPROVED,
        CRITERION_METRIC_THRESHOLD,
    }
)

CRITERION_SATISFIED = "satisfied"
CRITERION_UNSATISFIED = "unsatisfied"
CRITERION_INSUFFICIENT = "insufficient_evidence"

ASSESSMENT_SATISFIED = "satisfied"
ASSESSMENT_UNSATISFIED = "unsatisfied"
ASSESSMENT_INSUFFICIENT = "insufficient_evidence"


class PlanningError(Exception):
    """Raised for invalid plans, transitions or completion claims."""


# ---------------------------------------------------------------------------
# Domain drafts
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CriterionSpec:
    """One acceptance criterion for a goal."""

    criterion_type: str
    description: str = ""
    target: dict[str, Any] = field(default_factory=dict)
    evidence_requirement: str = "required"
    required: bool = True

    def validate(self) -> None:
        if self.criterion_type not in CRITERION_TYPES:
            raise PlanningError("Unknown criterion type: " + self.criterion_type)


@dataclass(slots=True)
class StepDraft:
    """One step in a plan draft."""

    step_key: str
    task: str
    agent_capability: str = "coordinator"
    depends_on: tuple[str, ...] = ()
    budget_max_cost: float = 5.0
    max_turns: int = 16
    max_retries: int = 0


@dataclass(slots=True)
class PlanDraft:
    """Acyclic plan: steps keyed by step_key plus edges."""

    steps: dict[str, StepDraft]

    def edges(self) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for step in self.steps.values():
            for dep in step.depends_on:
                result.append((dep, step.step_key))
        return result

    def validate_dag(self) -> None:
        """Reject cycles, self-deps, missing and duplicate targets."""
        if not self.steps:
            raise PlanningError("Plan must contain at least one step")
        for step in self.steps.values():
            if step.step_key in step.depends_on:
                raise PlanningError("Self-dependency on step: " + step.step_key)
            for dep in step.depends_on:
                if dep not in self.steps:
                    raise PlanningError(
                        "Step '" + step.step_key + "' depends on missing step '" + dep + "'"
                    )
        seen: set[str] = set()
        visiting: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise PlanningError("Cycle detected at step: " + key)
            if key in seen:
                return
            visiting.add(key)
            for dep in self.steps[key].depends_on:
                visit(dep)
            visiting.discard(key)
            seen.add(key)

        for key in self.steps:
            visit(key)

    def ready_set(self, statuses: dict[str, str]) -> list[str]:
        """Steps whose dependencies are all 'succeeded'/'skipped'.

        Only considers steps still in 'pending'; anything already terminal
        or running is excluded.
        """
        ready: list[str] = []
        for key, step in self.steps.items():
            if statuses.get(key) != STEP_PENDING:
                continue
            deps_satisfied = all(
                statuses.get(dep) in {STEP_SUCCEEDED, STEP_SKIPPED}
                for dep in step.depends_on
            )
            if deps_satisfied:
                ready.append(key)
        return ready

    def topological_order(self) -> list[str]:
        """Deterministic topological order (Kahn) for execution planning."""
        self.validate_dag()
        indegree = {key: len(step.depends_on) for key, step in self.steps.items()}
        children: dict[str, list[str]] = {key: [] for key in self.steps}
        for key, step in self.steps.items():
            for dep in step.depends_on:
                children[dep].append(key)
        queue = sorted(k for k, v in indegree.items() if v == 0)
        result: list[str] = []
        while queue:
            node = queue.pop(0)
            result.append(node)
            for child in children[node]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
                    queue.sort()
        if len(result) != len(self.steps):
            raise PlanningError("Plan is not a DAG")
        return result


# ---------------------------------------------------------------------------
# Goal / step state transitions
# ---------------------------------------------------------------------------

_GOAL_FROM = {
    GOAL_DRAFT: frozenset({GOAL_ACTIVE, GOAL_CANCELLED, GOAL_FAILED}),
    GOAL_ACTIVE: frozenset(
        {GOAL_NEEDS_INPUT, GOAL_BLOCKED, GOAL_COMPLETED, GOAL_FAILED, GOAL_CANCELLED}
    ),
    GOAL_NEEDS_INPUT: frozenset({GOAL_ACTIVE, GOAL_CANCELLED, GOAL_FAILED}),
    GOAL_BLOCKED: frozenset({GOAL_ACTIVE, GOAL_CANCELLED, GOAL_FAILED}),
    GOAL_COMPLETED: frozenset(),
    GOAL_FAILED: frozenset(),
    GOAL_CANCELLED: frozenset(),
}

_STEP_FROM = {
    STEP_PENDING: frozenset({STEP_READY, STEP_SKIPPED, STEP_CANCELLED}),
    STEP_READY: frozenset(
        {STEP_RUNNING, STEP_SUCCEEDED, STEP_FAILED, STEP_SKIPPED, STEP_CANCELLED}
    ),
    STEP_RUNNING: frozenset(
        {STEP_SUCCEEDED, STEP_FAILED, STEP_WAITING_REVIEW, STEP_CANCELLED}
    ),
    STEP_WAITING_REVIEW: frozenset(
        {STEP_SUCCEEDED, STEP_FAILED, STEP_RUNNING, STEP_CANCELLED}
    ),
    STEP_SUCCEEDED: frozenset(),
    STEP_FAILED: frozenset({STEP_PENDING}),
    STEP_SKIPPED: frozenset(),
    STEP_CANCELLED: frozenset(),
}


def transition_goal(current: str, target: str) -> str:
    if current not in GOAL_STATES or target not in GOAL_STATES:
        raise PlanningError("Unknown goal state: " + current + "/" + target)
    if target not in _GOAL_FROM[current]:
        raise PlanningError("Illegal goal transition: " + current + " -> " + target)
    return target


def transition_step(current: str, target: str) -> str:
    if current not in STEP_STATES or target not in STEP_STATES:
        raise PlanningError("Unknown step state: " + current + "/" + target)
    if target not in _STEP_FROM[current]:
        raise PlanningError("Illegal step transition: " + current + " -> " + target)
    return target


# ---------------------------------------------------------------------------
# Goal Interpreter (4.1): user request -> goal draft
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GoalDraft:
    title: str
    objective: str
    scope: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    criteria: list[CriterionSpec] = field(default_factory=list)
    complexity: str = "simple"  # simple | complex

    def validate(self) -> None:
        if not self.objective.strip():
            raise PlanningError("Goal objective is empty")
        for criterion in self.criteria:
            criterion.validate()


class GoalInterpreter:
    """Parse a user request into a structured goal draft.

    Simple conversational requests become a single implicit step
    ('simple'), avoiding over-planning; requests that mention multiple
    deliverables, external data or verification get a 'complex' draft and
    need explicit planner attention.  Low-confidence or ambiguous requests
    should ask the user to confirm before activation (caller's job).
    """

    _COMPLEX_MARKERS = (
        "同时",
        "以及验证",
        "并验证",
        "对比",
        "溯源",
        "传播",
        "多平台",
        "报告",
        "引用",
        "证据",
        "verify",
        "report",
        "compare",
        "propagation",
        "cross-platform",
    )

    def interpret(self, request: str) -> GoalDraft:
        text = (request or "").strip()
        if not text:
            raise PlanningError("Empty user request")
        complex_goal = any(
            marker.lower() in text.lower() for marker in self._COMPLEX_MARKERS
        )
        criteria: list[CriterionSpec] = []
        if complex_goal:
            criteria = [
                CriterionSpec(
                    criterion_type=CRITERION_ARTIFACT_EXISTS,
                    description="复杂目标产出可验证的产物",
                    target={"artifact_kind": "report"},
                    required=True,
                ),
                CriterionSpec(
                    criterion_type=CRITERION_CITATION_COVERAGE,
                    description="报告引用覆盖真实证据",
                    target={"min_coverage": 0.95},
                    required=True,
                ),
            ]
        return GoalDraft(
            title=text[:60],
            objective=text,
            scope={"request_source": "user"},
            complexity="complex" if complex_goal else "simple",
            criteria=criteria,
        )


# ---------------------------------------------------------------------------
# Planner (4.2): capability directory -> PlanDraft
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Capability:
    name: str
    description: str = ""
    available: bool = True


class Planner:
    """Build and validate plan drafts against a capability directory.

    'capabilities' maps an agent/skill/tool capability name to whether it
    is executable.  The planner never lets the LLM declare an unavailable
    capability as runnable.
    """

    def __init__(self, capabilities: dict[str, Capability] | None = None) -> None:
        self._capabilities = dict(capabilities or {})

    def register(self, name: str, description: str = "", available: bool = True) -> None:
        self._capabilities[name] = Capability(name, description, available)

    def capabilities(self) -> list[str]:
        return sorted(self._capabilities)

    def validate_plan(
        self,
        plan: PlanDraft,
        *,
        max_steps: int = 64,
        max_total_budget: float = 100.0,
    ) -> PlanDraft:
        plan.validate_dag()
        if len(plan.steps) > max_steps:
            raise PlanningError(
                "Plan exceeds max step count: " + str(len(plan.steps))
            )
        total = sum(step.budget_max_cost for step in plan.steps.values())
        if total > max_total_budget:
            raise PlanningError(
                "Plan budget exceeds max total: " + str(total) + " > " + str(max_total_budget)
            )
        for step in plan.steps.values():
            capability = self._capabilities.get(step.agent_capability)
            if capability is None:
                raise PlanningError(
                    "Capability not in directory: " + step.agent_capability
                )
            if not capability.available:
                raise PlanningError(
                    "Capability unavailable: " + step.agent_capability
                )
        return plan

    def default_capabilities(self) -> Planner:
        """Harness's known agent/skill capabilities (read-only core)."""
        for name in (
            "coordinator",
            "opinion",
            "propagation",
            "verification",
            "evidence_critic",
            "report",
            "citation_validator",
            "social_crawl",
            "rag_search",
            "memory",
        ):
            self.register(name)
        return self


# ---------------------------------------------------------------------------
# Completion Verifier (4.4): deterministic checks first.
# ---------------------------------------------------------------------------

EvidenceProvider = Callable[[str, dict[str, Any]], Any]


class CompletionVerifier:
    """Hierarchical completion verification.

    Deterministic schema/count/citation checks run first; domain
    evaluations or human review run second; an LLM judge is only ever a
    helper.  Output is 'satisfied | unsatisfied | insufficient_evidence'
    plus a per-criterion breakdown and a gap list.
    """

    def __init__(self, evidence: EvidenceProvider | None = None) -> None:
        self._evidence = evidence

    async def verify(
        self,
        goal: Any,
        criteria: list[CriterionSpec],
        *,
        evidence: EvidenceProvider | None = None,
    ) -> dict[str, Any]:
        provider = evidence or self._evidence
        if provider is None:
            raise PlanningError("CompletionVerifier requires an evidence provider")
        results: dict[str, Any] = {}
        gaps: list[str] = []
        for criterion in criteria:
            criterion.validate()
            outcome = await self._check_criterion(criterion, goal, provider)
            key = criterion.criterion_type + ":" + criterion.description[:40]
            results[key] = outcome
            if outcome["status"] == CRITERION_UNSATISFIED:
                gaps.append(criterion.description)
            elif (
                outcome["status"] == CRITERION_INSUFFICIENT
                and criterion.evidence_requirement == "required"
            ):
                gaps.append("证据不足: " + criterion.description)
        required_results = [
            results[c.criterion_type + ":" + c.description[:40]]
            for c in criteria
            if c.required
        ]
        if all(r["status"] == CRITERION_SATISFIED for r in required_results):
            verdict = ASSESSMENT_SATISFIED
        elif any(r["status"] == CRITERION_UNSATISFIED for r in required_results):
            verdict = ASSESSMENT_UNSATISFIED
        else:
            verdict = ASSESSMENT_INSUFFICIENT
        return {
            "result": verdict,
            "criterion_results": results,
            "gaps": gaps,
            "verifier": "deterministic",
        }

    async def _check_criterion(
        self,
        criterion: CriterionSpec,
        goal: Any,
        provider: EvidenceProvider,
    ) -> dict[str, Any]:
        target = criterion.target or {}
        if criterion.criterion_type == CRITERION_ARTIFACT_EXISTS:
            return await self._check_artifact_exists(target, goal, provider)
        if criterion.criterion_type == CRITERION_SCHEMA_VALID:
            return await self._check_schema_valid(target, goal, provider)
        if criterion.criterion_type == CRITERION_CITATION_COVERAGE:
            return await self._check_citation_coverage(target, goal, provider)
        if criterion.criterion_type == CRITERION_TOOL_SUCCEEDED:
            return await self._check_tool_succeeded(target, goal, provider)
        if criterion.criterion_type == CRITERION_HUMAN_APPROVED:
            return await self._check_human_approved(target, goal, provider)
        if criterion.criterion_type == CRITERION_METRIC_THRESHOLD:
            return await self._check_metric_threshold(target, goal, provider)
        return {"status": CRITERION_INSUFFICIENT, "reason": "unsupported criterion"}

    async def _check_artifact_exists(
        self,
        target: dict[str, Any],
        goal: Any,
        provider: EvidenceProvider,
    ) -> dict[str, Any]:
        kind = str(target.get("artifact_kind") or "report")
        case_id = str(getattr(goal, "case_id", "") or target.get("case_id") or "")
        try:
            artifacts = await provider("artifacts", {"case_id": case_id})
        except Exception as exc:  # noqa: BLE001
            return {
                "status": CRITERION_INSUFFICIENT,
                "reason": "evidence lookup failed: " + str(exc),
            }
        matched = [
            artifact
            for artifact in artifacts or []
            if str(
                getattr(artifact, "kind", "")
                or (artifact.get("kind", "") if isinstance(artifact, dict) else "")
            )
            == kind
        ]
        if not matched:
            return {
                "status": CRITERION_UNSATISFIED,
                "reason": "no artifact of kind: " + kind,
                "found": 0,
            }
        return {
            "status": CRITERION_SATISFIED,
            "found": len(matched),
            "artifact_ids": [
                str(
                    getattr(a, "id", "")
                    or (a.get("id", "") if isinstance(a, dict) else "")
                )
                for a in matched
            ],
        }

    async def _check_schema_valid(
        self,
        target: dict[str, Any],
        goal: Any,
        provider: EvidenceProvider,
    ) -> dict[str, Any]:
        artifact_id = str(target.get("artifact_id") or "")
        if not artifact_id:
            return {
                "status": CRITERION_INSUFFICIENT,
                "reason": "schema_valid requires target.artifact_id",
            }
        try:
            artifact = await provider("artifact", {"artifact_id": artifact_id})
        except Exception as exc:  # noqa: BLE001
            return {
                "status": CRITERION_INSUFFICIENT,
                "reason": "artifact lookup failed: " + str(exc),
            }
        if artifact is None:
            return {
                "status": CRITERION_INSUFFICIENT,
                "reason": "artifact not found: " + artifact_id,
            }
        required_fields = target.get("required_fields") or []
        data = artifact or {}
        missing = [
            field_name for field_name in required_fields if field_name not in data
        ]
        if missing:
            return {
                "status": CRITERION_UNSATISFIED,
                "missing_fields": missing,
                "reason": "schema missing fields: " + ",".join(missing),
            }
        return {"status": CRITERION_SATISFIED, "artifact_id": artifact_id}

    async def _check_citation_coverage(
        self,
        target: dict[str, Any],
        goal: Any,
        provider: EvidenceProvider,
    ) -> dict[str, Any]:
        artifact_id = str(target.get("artifact_id") or "")
        min_coverage = float(target.get("min_coverage") or 0.95)
        if not artifact_id:
            case_id = str(getattr(goal, "case_id", "") or "")
            try:
                artifacts = await provider("artifacts", {"case_id": case_id})
            except Exception as exc:  # noqa: BLE001
                return {
                    "status": CRITERION_INSUFFICIENT,
                    "reason": "artifact lookup failed: " + str(exc),
                }
            reports = [
                artifact
                for artifact in artifacts or []
                if str(
                    getattr(artifact, "kind", "")
                    or (
                        artifact.get("kind", "")
                        if isinstance(artifact, dict)
                        else ""
                    )
                )
                == str(target.get("artifact_kind") or "report")
            ]
            if not reports:
                return {
                    "status": CRITERION_UNSATISFIED,
                    "reason": "no report artifact available for citation check",
                }
            artifact_id = str(
                getattr(reports[0], "id", "")
                or (
                    reports[0].get("id", "")
                    if isinstance(reports[0], dict)
                    else ""
                )
            )
        try:
            data = await provider(
                "artifact_data",
                {
                    "artifact_id": artifact_id,
                    "case_id": str(getattr(goal, "case_id", "") or ""),
                },
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "status": CRITERION_INSUFFICIENT,
                "reason": "artifact data lookup failed: " + str(exc),
            }
        cited = data.get("cited") if isinstance(data, dict) else None
        resolved = data.get("resolved") if isinstance(data, dict) else None
        if cited is None or resolved is None:
            return {
                "status": CRITERION_INSUFFICIENT,
                "reason": "artifact data lacks cited/resolved counts",
            }
        coverage = (resolved / cited) if cited else 1.0
        if coverage + 1e-9 < min_coverage:
            return {
                "status": CRITERION_UNSATISFIED,
                "coverage": round(coverage, 4),
                "min_coverage": min_coverage,
                "reason": "citation coverage below threshold",
            }
        return {
            "status": CRITERION_SATISFIED,
            "coverage": round(coverage, 4),
            "cited": cited,
            "resolved": resolved,
        }

    async def _check_tool_succeeded(
        self,
        target: dict[str, Any],
        goal: Any,
        provider: EvidenceProvider,
    ) -> dict[str, Any]:
        tool_name = str(target.get("tool_name") or "")
        if not tool_name:
            return {
                "status": CRITERION_INSUFFICIENT,
                "reason": "tool_succeeded requires target.tool_name",
            }
        try:
            calls = await provider("tool_calls", {"tool_name": tool_name})
        except Exception as exc:  # noqa: BLE001
            return {
                "status": CRITERION_INSUFFICIENT,
                "reason": "tool calls lookup failed: " + str(exc),
            }
        succeeded = [
            call
            for call in calls or []
            if str(
                getattr(call, "status", "")
                or (call.get("status", "") if isinstance(call, dict) else "")
            )
            == "completed"
        ]
        if not succeeded:
            return {
                "status": CRITERION_UNSATISFIED,
                "reason": "no successful call of tool: " + tool_name,
                "found": len(calls or []),
            }
        return {
            "status": CRITERION_SATISFIED,
            "successful_calls": len(succeeded),
            "call_ids": [
                str(
                    getattr(c, "id", "")
                    or (c.get("id", "") if isinstance(c, dict) else "")
                )
                for c in succeeded[:5]
            ],
        }

    async def _check_human_approved(
        self,
        target: dict[str, Any],
        goal: Any,
        provider: EvidenceProvider,
    ) -> dict[str, Any]:
        try:
            approvals = await provider("approvals", {})
        except Exception as exc:  # noqa: BLE001
            return {
                "status": CRITERION_INSUFFICIENT,
                "reason": "approvals lookup failed: " + str(exc),
            }
        approved = [
            a
            for a in approvals or []
            if str(
                getattr(a, "status", "")
                or (a.get("status", "") if isinstance(a, dict) else "")
            )
            in {"approved", "approved_with_edits"}
        ]
        if not approved:
            return {
                "status": CRITERION_UNSATISFIED,
                "reason": "no human approval recorded",
            }
        return {"status": CRITERION_SATISFIED, "approval_count": len(approved)}

    async def _check_metric_threshold(
        self,
        target: dict[str, Any],
        goal: Any,
        provider: EvidenceProvider,
    ) -> dict[str, Any]:
        metric = str(target.get("metric") or "")
        operator = str(target.get("operator") or ">=")
        threshold = float(target.get("threshold") or 0)
        if not metric:
            return {
                "status": CRITERION_INSUFFICIENT,
                "reason": "metric_threshold requires target.metric",
            }
        try:
            evaluations = await provider("evaluations", {"metric": metric})
        except Exception as exc:  # noqa: BLE001
            return {
                "status": CRITERION_INSUFFICIENT,
                "reason": "evaluations lookup failed: " + str(exc),
            }
        values = [
            float(
                getattr(e, "score", 0)
                or (e.get("score", 0) if isinstance(e, dict) else 0)
            )
            for e in evaluations or []
        ]
        if not values:
            return {
                "status": CRITERION_INSUFFICIENT,
                "reason": "no evaluation rows for metric: " + metric,
            }
        latest = values[-1]
        passed = {
            ">=": latest >= threshold,
            "<=": latest <= threshold,
            ">": latest > threshold,
            "<": latest < threshold,
            "==": abs(latest - threshold) < 1e-9,
        }.get(operator, False)
        if not passed:
            return {
                "status": CRITERION_UNSATISFIED,
                "value": latest,
                "threshold": threshold,
                "operator": operator,
                "reason": "metric below threshold",
            }
        return {
            "status": CRITERION_SATISFIED,
            "value": latest,
            "threshold": threshold,
            "operator": operator,
        }
