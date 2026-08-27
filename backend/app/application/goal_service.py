"""M17 application service: goal lifecycle, plan building and completion.

Orchestrates the pure planning logic (``app.services.planning``) against the
repository.  Agent dispatch itself stays in :class:`GraphWorker`; this service
records step->run associations and completion evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.services.planning import (
    ASSESSMENT_SATISFIED,
    CRITERION_SATISFIED,
    GOAL_ACTIVE,
    GOAL_CANCELLED,
    GOAL_COMPLETED,
    STEP_CANCELLED,
    STEP_FAILED,
    STEP_PENDING,
    STEP_READY,
    STEP_RUNNING,
    STEP_SKIPPED,
    STEP_SUCCEEDED,
    STEP_WAITING_REVIEW,
    CompletionVerifier,
    CriterionSpec,
    GoalInterpreter,
    PlanDraft,
    Planner,
    PlanningError,
    StepDraft,
    transition_goal,
    transition_step,
)


class GoalServiceError(ApplicationError):
    pass


class GoalService:
    def __init__(self, repository: ApplicationRepository) -> None:
        self._repository = repository
        self._interpreter = GoalInterpreter()
        self._planner = Planner().default_capabilities()
        self._verifier = CompletionVerifier()

    # -- goal lifecycle -----------------------------------------------------

    async def create_goal(
        self,
        *,
        case_id: str,
        objective: str,
        constraints: list[str] | None = None,
        priority: str = "normal",
    ) -> dict[str, object]:
        draft = self._interpreter.interpret(objective)
        goal = await self._repository.create_goal(
            case_id=case_id,
            title=draft.title,
            objective=objective,
            scope=draft.scope,
            constraints=constraints or draft.constraints,
            priority=priority,
        )
        criteria = await self._repository.add_acceptance_criteria(
            goal.id,
            [
                {
                    "criterion_type": c.criterion_type,
                    "description": c.description,
                    "target": c.target,
                    "evidence_requirement": c.evidence_requirement,
                    "required": c.required,
                }
                for c in draft.criteria
            ],
        )
        if not criteria and draft.complexity == "complex":
            criteria = await self._repository.add_acceptance_criteria(
                goal.id,
                [
                    {
                        "criterion_type": "artifact_exists",
                        "description": "复杂目标产出可验证的产物",
                        "target": {"artifact_kind": "report"},
                        "required": True,
                    }
                ],
            )
        await self._repository.update_goal_status(goal.id, status=GOAL_ACTIVE)
        activated = await self._repository.get_goal(goal.id)
        return {
            "goal": activated,
            "criteria": criteria,
            "complexity": draft.complexity,
        }

    async def transition_goal(
        self, goal_id: str, target: str, reason: str | None = None
    ) -> Any:
        goal = await self._repository.get_goal(goal_id)
        try:
            transition_goal(goal.status, target)
        except PlanningError as exc:
            raise GoalServiceError(str(exc), code="goal_transition_invalid") from exc
        if target == GOAL_CANCELLED and not reason:
            raise GoalServiceError(
                "取消目标必须提供原因", code="goal_cancel_reason_required"
            )
        return await self._repository.update_goal_status(
            goal_id, status=target, cancelled_reason=reason
        )

    # -- plan building ------------------------------------------------------

    async def create_plan(
        self,
        *,
        goal_id: str,
        steps: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        planner: str = "deterministic",
    ) -> dict[str, object]:
        goal = await self._repository.get_goal(goal_id)
        if goal.status != GOAL_ACTIVE:
            raise GoalServiceError(
                "Goal is not active: " + goal.status,
                code="goal_not_active",
            )
        versions = await self._repository.list_plan_versions(goal_id)
        next_version = (max((v.version for v in versions), default=0)) + 1
        draft_steps: dict[str, StepDraft] = {}
        for item in steps:
            key = str(item.get("step_key") or "")
            if not key:
                raise GoalServiceError(
                    "Plan step requires step_key", code="plan_step_key_required"
                )
            if key in draft_steps:
                raise GoalServiceError(
                    "Duplicate step_key: " + key, code="plan_duplicate_step"
                )
            draft_steps[key] = StepDraft(
                step_key=key,
                task=str(item.get("task") or ""),
                agent_capability=str(item.get("agent_capability") or "coordinator"),
                depends_on=tuple(str(d) for d in (item.get("depends_on") or [])),
                budget_max_cost=float(item.get("budget_max_cost") or 5.0),
                max_turns=int(item.get("max_turns") or 16),
                max_retries=int(item.get("max_retries") or 0),
            )
        for edge in edges:
            source = str(edge.get("source_step_key") or "")
            target = str(edge.get("target_step_key") or "")
            if source not in draft_steps or target not in draft_steps:
                raise GoalServiceError(
                    "Plan edge references unknown step",
                    code="plan_edge_unknown_step",
                )
            target_step = draft_steps[target]
            target_step.depends_on = tuple(
                dict.fromkeys((*target_step.depends_on, source))
            )
        draft = PlanDraft(steps=draft_steps)
        try:
            draft.validate_dag()
            self._planner.validate_plan(draft)
        except PlanningError as exc:
            raise GoalServiceError(str(exc), code="plan_invalid") from exc

        plan_version = await self._repository.create_plan_version(
            goal_id=goal_id, version=next_version, planner=planner
        )
        created = await self._repository.add_plan_step_batch(
            plan_version_id=plan_version.id,
            steps=[
                {
                    "step_key": s.step_key,
                    "task": s.task,
                    "agent_capability": s.agent_capability,
                    "budget_max_cost": s.budget_max_cost,
                    "max_turns": s.max_turns,
                    "max_retries": s.max_retries,
                }
                for s in draft_steps.values()
            ],
            declared_by=planner,
        )
        step_id_by_key = {record.step_key: record.id for record in created}
        for edge in edges:
            source = str(edge.get("source_step_key") or "")
            target = str(edge.get("target_step_key") or "")
            if source not in step_id_by_key or target not in step_id_by_key:
                raise GoalServiceError(
                    "Plan edge references unknown step",
                    code="plan_edge_unknown_step",
                )
            await self._repository.add_plan_edge(
                plan_version_id=plan_version.id,
                source_step_key=source,
                target_step_key=target,
                edge_type=str(edge.get("edge_type") or "dependency"),
            )
        await self._repository.update_plan_version_status(
            plan_version.id, status="active", frozen_at=datetime.now(UTC)
        )
        return {
            "plan_version": plan_version,
            "steps": created,
            "step_id_by_key": step_id_by_key,
        }

    # -- step execution hooks -----------------------------------------------

    async def declare_step(
        self,
        step_id: str,
        *,
        action: str,
        reason: str | None = None,
    ) -> Any:
        """Worker/human declares a step outcome through the state machine.

        pending 步骤声明完成时先隐式提升为 ready（依赖满足由调用方保证，
        ready 是计算态而非持久化独占态），再走状态机。
        """
        step = await self._repository.get_plan_step(step_id)
        if step.status == STEP_PENDING:
            status = await self.plan_status(step.plan_version_id)
            if step.step_key not in status["ready_steps"]:
                raise GoalServiceError(
                    "Step dependencies are not satisfied",
                    code="plan_step_dependencies_unsatisfied",
                )
            step = await self._repository.update_plan_step(
                step_id, status=STEP_READY
            )
        mapping = {
            "start": STEP_RUNNING,
            "wait": STEP_WAITING_REVIEW,
            "succeed": STEP_SUCCEEDED,
            "fail": STEP_FAILED,
            "skip": STEP_SKIPPED,
            "cancel": STEP_CANCELLED,
        }
        target = mapping.get(action)
        if target is None:
            raise GoalServiceError(
                "Unknown step action: " + action, code="plan_step_action_unknown"
            )
        try:
            transition_step(step.status, target)
        except PlanningError as exc:
            raise GoalServiceError(
                str(exc), code="plan_step_transition_invalid"
            ) from exc
        if action in {"skip", "cancel"} and not reason:
            raise GoalServiceError(
                "跳过或取消步骤必须提供理由", code="plan_step_reason_required"
            )
        return await self._repository.update_plan_step(
            step_id,
            status=target,
            completion_declared_by=(
                "human" if action in {"skip", "cancel"} else "worker"
            ),
        )

    async def attach_run(self, step_id: str, run_id: str) -> Any:
        step = await self._repository.get_plan_step(step_id)
        try:
            transition_step(step.status, STEP_RUNNING)
        except PlanningError as exc:
            if step.status not in {STEP_READY, STEP_RUNNING}:
                raise GoalServiceError(
                    str(exc), code="plan_step_transition_invalid"
                ) from exc
        return await self._repository.update_plan_step(
            step_id, status=STEP_RUNNING, run_id=run_id
        )

    async def add_evidence(
        self,
        *,
        step_id: str,
        evidence_type: str,
        ref_id: str,
        ref_kind: str,
        payload: dict[str, object] | None = None,
    ) -> Any:
        # M17: 引用必须真实存在且属于当前案件（不允许只记录悬空引用）。
        await self._validate_evidence_ref(step_id, ref_kind, ref_id)
        return await self._repository.add_step_evidence(
            step_id=step_id,
            evidence_type=evidence_type,
            ref_id=ref_id,
            ref_kind=ref_kind,
            payload=payload,
        )

    async def _validate_evidence_ref(
        self, step_id: str, ref_kind: str, ref_id: str
    ) -> None:
        """统一验证 ref_kind/ref_id：资源存在且 case_id 与目标一致。"""
        if not ref_kind or not ref_id:
            raise GoalServiceError(
                "evidence ref_kind and ref_id are required",
                code="evidence_ref_invalid",
            )
        step = await self._repository.get_plan_step(step_id)
        plan_version = await self._repository.get_plan_version(step.plan_version_id)
        goal = await self._repository.get_goal(plan_version.goal_id)
        case_id = goal.case_id

        def _load(kind: str) -> Any:
            if kind == "artifact":
                return self._repository.get_artifact(ref_id)
            if kind == "claim":
                return self._repository.get_claim(ref_id)
            if kind == "run":
                return self._repository.get_agent_run(ref_id)
            if kind == "narrative":
                return self._repository.get_narrative(ref_id)
            if kind == "review_item":
                return self._repository.get_review_item(ref_id)
            raise GoalServiceError(
                f"unsupported evidence ref_kind {ref_kind}",
                code="evidence_ref_kind_unsupported",
            )

        record = await _load(ref_kind)
        if record is None:
            raise GoalServiceError(
                f"{ref_kind} not found: {ref_id}",
                code="evidence_ref_not_found",
            )
        if getattr(record, "case_id", None) != case_id:
            raise GoalServiceError(
                f"evidence {ref_kind} belongs to a different case",
                code="evidence_ref_case_mismatch",
            )

    # -- completion verification ---------------------------------------------

    async def assess_goal(
        self, goal_id: str, plan_version_id: str
    ) -> dict[str, object]:
        goal = await self._repository.get_goal(goal_id)
        plan_version = await self._repository.get_plan_version(plan_version_id)
        if plan_version.goal_id != goal_id:
            raise GoalServiceError(
                "Plan does not belong to goal", code="plan_goal_mismatch"
            )
        criteria_rows = await self._repository.list_acceptance_criteria(goal_id)
        criteria = [
            CriterionSpec(
                criterion_type=row.criterion_type,
                description=row.description,
                target=dict(row.target or {}),
                evidence_requirement=row.evidence_requirement,
                required=row.required,
            )
            for row in criteria_rows
        ]

        async def evidence_provider(
            kind: str, query: dict[str, Any]
        ) -> Any:
            return await self._resolve_evidence(kind, query)

        result = await self._verifier.verify(
            goal, criteria, evidence=evidence_provider
        )
        steps = await self._repository.list_plan_steps(plan_version_id)
        unfinished = [
            step.step_key
            for step in steps
            if step.status not in {STEP_SUCCEEDED, STEP_SKIPPED}
        ]
        if unfinished:
            result = {
                **result,
                "result": "insufficient_evidence",
                "gaps": [
                    *list(result.get("gaps") or []),
                    "unfinished plan steps: " + ", ".join(unfinished),
                ],
            }
        assessment = await self._repository.create_completion_assessment(
            goal_id=goal_id,
            plan_version_id=plan_version_id,
            verifier=result["verifier"],
            result=result["result"],
            criterion_results=result["criterion_results"],
            gaps=result["gaps"],
        )
        if result["result"] == ASSESSMENT_SATISFIED:
            await self._repository.update_goal_status(
                goal_id, status=GOAL_COMPLETED
            )
            for row in criteria_rows:
                await self._repository.update_criterion_status(
                    row.id, status=CRITERION_SATISFIED
                )
        return {
            "assessment": assessment,
            "result": result["result"],
            "criterion_results": result["criterion_results"],
            "gaps": result["gaps"],
        }

    async def _resolve_evidence(
        self, kind: str, query: dict[str, Any]
    ) -> Any:
        if kind == "artifacts":
            case_id = str(query.get("case_id") or "")
            if not case_id:
                return []
            return await self._repository.list_artifacts(case_id)
        if kind == "artifact":
            artifact = await self._repository.get_artifact(
                str(query.get("artifact_id") or "")
            )
            case_id = str(query.get("case_id") or "")
            if case_id and artifact.case_id != case_id:
                return None
            return artifact
        if kind == "artifact_data":
            artifact_id = str(query.get("artifact_id") or "")
            artifact = await self._repository.get_artifact(artifact_id)
            case_id = str(query.get("case_id") or "")
            if case_id and artifact.case_id != case_id:
                return {}
            data = artifact.data if hasattr(artifact, "data") else {}
            if not isinstance(data, dict):
                return {}
            return {
                "cited": data.get("cited"),
                "resolved": data.get("resolved"),
            }
        if kind == "tool_calls":
            return []
        if kind == "approvals":
            return []
        if kind == "evaluations":
            metric = str(query.get("metric") or "")
            if not metric:
                return []
            return await self._repository.list_evaluations(metric=metric)
        return []

    # -- status views --------------------------------------------------------

    async def plan_status(self, plan_version_id: str) -> dict[str, object]:
        plan_version = await self._repository.get_plan_version(plan_version_id)
        steps = await self._repository.list_plan_steps(plan_version_id)
        edges = await self._repository.list_plan_edges(plan_version_id)
        statuses = {record.step_key: record.status for record in steps}
        draft = PlanDraft(
            steps={
                record.step_key: StepDraft(
                    step_key=record.step_key,
                    task=record.task,
                    agent_capability=record.agent_capability,
                    depends_on=tuple(
                        edge.source_step_key
                        for edge in edges
                        if edge.target_step_key == record.step_key
                    ),
                )
                for record in steps
            }
        )
        try:
            ready = draft.ready_set(statuses)
            topological = draft.topological_order()
        except PlanningError:
            ready = []
            topological = []
        return {
            "plan_version": plan_version,
            "steps": steps,
            "edges": edges,
            "ready_steps": ready,
            "topological_order": topological,
        }

