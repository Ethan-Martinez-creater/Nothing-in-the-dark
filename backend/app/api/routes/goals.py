"""M17: explicit goals, plan graphs and completion API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_container
from app.application.goal_service import GoalService, GoalServiceError
from app.bootstrap import ApplicationContainer
from app.services.planning import (
    GOAL_ACTIVE,
    GOAL_BLOCKED,
    GOAL_CANCELLED,
    GOAL_FAILED,
    GOAL_NEEDS_INPUT,
)

router = APIRouter()
goal_router = APIRouter()

_GOAL_TARGETS = frozenset(
    {GOAL_ACTIVE, GOAL_NEEDS_INPUT, GOAL_BLOCKED, GOAL_CANCELLED, GOAL_FAILED}
)


def _goal_service(container: ApplicationContainer) -> GoalService:
    return container.goal_service


@router.post("/{case_id}/goals")
async def create_goal(
    case_id: str,
    body: dict[str, Any],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    objective = str(body.get("objective") or "").strip()
    if not objective:
        raise HTTPException(status_code=422, detail="objective is required")
    service = _goal_service(container)
    try:
        result = await service.create_goal(
            case_id=case_id,
            objective=objective,
            constraints=[str(c) for c in (body.get("constraints") or [])],
            priority=str(body.get("priority") or "normal"),
        )
    except GoalServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    goal = result["goal"]
    return {
        "goal": _goal_payload(goal),
        "criteria": [
            {
                "id": c.id,
                "criterion_type": c.criterion_type,
                "description": c.description,
                "target": c.target,
                "status": c.status,
                "required": c.required,
            }
            for c in result["criteria"]
        ],
        "complexity": result["complexity"],
    }


@router.get("/{case_id}/goals")
async def list_goals(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    records = await container.repository.list_goals(case_id)
    return [_goal_payload(g) for g in records]


@goal_router.get("/{goal_id}")
async def get_goal_detail(
    goal_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    goal = await container.repository.get_goal(goal_id)
    criteria = await container.repository.list_acceptance_criteria(goal_id)
    versions = await container.repository.list_plan_versions(goal_id)
    assessments = await container.repository.list_completion_assessments(goal_id)
    return {
        "goal": _goal_payload(goal),
        "criteria": [
            {
                "id": c.id,
                "criterion_type": c.criterion_type,
                "description": c.description,
                "target": c.target,
                "status": c.status,
                "required": c.required,
            }
            for c in criteria
        ],
        "plan_versions": [
            {
                "id": v.id,
                "version": v.version,
                "status": v.status,
                "planner": v.planner,
                "frozen_at": v.frozen_at.isoformat() if v.frozen_at else None,
            }
            for v in versions
        ],
        "assessments": [
            {
                "id": a.id,
                "plan_version_id": a.plan_version_id,
                "verifier": a.verifier,
                "result": a.result,
                "gaps": a.gaps,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in assessments
        ],
    }


@goal_router.post("/{goal_id}/transition")
async def transition_goal(
    goal_id: str,
    body: dict[str, Any],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    target = str(body.get("target") or "")
    if target not in _GOAL_TARGETS:
        raise HTTPException(status_code=422, detail="unsupported goal transition")
    service = _goal_service(container)
    raw_reason = body.get("reason")
    try:
        record = await service.transition_goal(
            goal_id, target, reason=(str(raw_reason) if raw_reason else None)
        )
    except GoalServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return _goal_payload(record)


@goal_router.post("/{goal_id}/plans")
async def create_plan(
    goal_id: str,
    body: dict[str, Any],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    steps = body.get("steps") or []
    edges = body.get("edges") or []
    service = _goal_service(container)
    try:
        result = await service.create_plan(
            goal_id=goal_id,
            steps=[dict(s) for s in steps],
            edges=[dict(e) for e in edges],
            planner=str(body.get("planner") or "deterministic"),
        )
    except GoalServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    plan_version = result["plan_version"]
    return {
        "plan_version": {
            "id": plan_version.id,
            "goal_id": plan_version.goal_id,
            "version": plan_version.version,
            "status": plan_version.status,
            "planner": plan_version.planner,
        },
        "steps": [
            {
                "id": s.id,
                "step_key": s.step_key,
                "task": s.task,
                "agent_capability": s.agent_capability,
                "status": s.status,
                "budget_max_cost": s.budget_max_cost,
                "run_id": s.run_id,
            }
            for s in result["steps"]
        ],
        "step_id_by_key": result["step_id_by_key"],
    }


@goal_router.get("/plans/{plan_version_id}")
async def get_plan(
    plan_version_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    service = _goal_service(container)
    status = await service.plan_status(plan_version_id)
    plan_version = status["plan_version"]
    return {
        "plan_version": {
            "id": plan_version.id,
            "goal_id": plan_version.goal_id,
            "version": plan_version.version,
            "status": plan_version.status,
            "planner": plan_version.planner,
        },
        "steps": [
            {
                "id": s.id,
                "step_key": s.step_key,
                "task": s.task,
                "agent_capability": s.agent_capability,
                "status": s.status,
                "budget_max_cost": s.budget_max_cost,
                "run_id": s.run_id,
                "retry_count": s.retry_count,
            }
            for s in status["steps"]
        ],
        "edges": [
            {
                "source_step_key": e.source_step_key,
                "target_step_key": e.target_step_key,
                "edge_type": e.edge_type,
            }
            for e in status["edges"]
        ],
        "ready_steps": status["ready_steps"],
        "topological_order": status["topological_order"],
    }


@goal_router.post("/plans/{plan_version_id}/steps/{step_id}/declare")
async def declare_step(
    plan_version_id: str,
    step_id: str,
    body: dict[str, Any],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    step = await container.repository.get_plan_step(step_id)
    if step.plan_version_id != plan_version_id:
        raise HTTPException(status_code=404, detail="plan step not found")
    service = _goal_service(container)
    try:
        raw_reason = body.get("reason")
        record = await service.declare_step(
            step_id,
            action=str(body.get("action") or ""),
            reason=(str(raw_reason) if raw_reason else None),
        )
    except GoalServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {
        "id": record.id,
        "step_key": record.step_key,
        "status": record.status,
        "completion_declared_by": record.completion_declared_by,
    }


@goal_router.post("/plans/{plan_version_id}/steps/{step_id}/evidence")
async def add_step_evidence(
    plan_version_id: str,
    step_id: str,
    body: dict[str, Any],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    step = await container.repository.get_plan_step(step_id)
    if step.plan_version_id != plan_version_id:
        raise HTTPException(status_code=404, detail="plan step not found")
    service = _goal_service(container)
    record = await service.add_evidence(
        step_id=step_id,
        evidence_type=str(body.get("evidence_type") or "artifact"),
        ref_id=str(body.get("ref_id") or ""),
        ref_kind=str(body.get("ref_kind") or ""),
        payload=dict(body.get("payload") or {}),
    )
    return {
        "id": record.id,
        "step_id": record.step_id,
        "evidence_type": record.evidence_type,
        "ref_id": record.ref_id,
    }


@goal_router.post("/{goal_id}/assess")
async def assess_goal(
    goal_id: str,
    body: dict[str, Any],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    plan_version_id = str(body.get("plan_version_id") or "")
    if not plan_version_id:
        raise HTTPException(status_code=422, detail="plan_version_id is required")
    service = _goal_service(container)
    try:
        result = await service.assess_goal(goal_id, plan_version_id)
    except GoalServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    assessment = result["assessment"]
    return {
        "assessment": {
            "id": assessment.id,
            "goal_id": assessment.goal_id,
            "plan_version_id": assessment.plan_version_id,
            "verifier": assessment.verifier,
            "result": assessment.result,
            "criterion_results": assessment.criterion_results,
            "gaps": assessment.gaps,
            "created_at": (
                assessment.created_at.isoformat() if assessment.created_at else None
            ),
        },
        "gaps": result["gaps"],
    }


def _goal_payload(goal: Any) -> dict[str, object]:
    return {
        "id": goal.id,
        "case_id": goal.case_id,
        "title": goal.title,
        "objective": goal.objective,
        "constraints": goal.constraints,
        "priority": goal.priority,
        "status": goal.status,
        "version": goal.version,
        "source": goal.source,
        "created_at": goal.created_at.isoformat() if goal.created_at else None,
        "updated_at": goal.updated_at.isoformat() if goal.updated_at else None,
    }
