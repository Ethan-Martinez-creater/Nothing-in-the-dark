"""M20: evaluation datasets, runs, gates and online drift API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_container
from app.application.evaluation_service import EvaluationServiceError
from app.bootstrap import ApplicationContainer

router = APIRouter()


def _service(container: ApplicationContainer):
    return container.evaluation_service


@router.post("/datasets")
async def register_dataset(
    body: dict[str, Any],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    service = _service(container)
    try:
        result = await service.register_dataset(
            manifest=dict(body.get("manifest") or {}),
            examples=[dict(e) for e in (body.get("examples") or [])],
        )
    except EvaluationServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    manifest = result["manifest"]
    return {
        "manifest": {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "task": manifest.task,
            "license": manifest.license,
            "content_hash": manifest.content_hash,
            "train_holdout": manifest.train_holdout,
        },
        "example_count": result["example_count"],
        "content_hash": result["content_hash"],
    }


@router.get("/datasets")
async def list_datasets(
    limit: int = Query(default=50, ge=1, le=200),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    records = await container.repository.list_dataset_manifests(limit=limit)
    return [
        {
            "id": r.id,
            "name": r.name,
            "version": r.version,
            "task": r.task,
            "license": r.license,
            "content_hash": r.content_hash,
            "example_count": r.example_count,
            "train_holdout": r.train_holdout,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]


@router.post("/runs")
async def run_evaluation(
    body: dict[str, Any],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    service = _service(container)
    try:
        result = await service.run_evaluation(
            suite=str(body.get("suite") or "default"),
            candidate_version=str(body.get("candidate_version") or "candidate"),
            baseline_version=str(body.get("baseline_version") or "baseline"),
            dataset_manifest_id=str(body.get("dataset_manifest_id") or ""),
            evaluator_names=[
                str(n) for n in (body.get("evaluator_names") or [])
            ] or None,
            commit=str(body.get("commit") or ""),
            config=dict(body.get("config") or {}),
        )
    except EvaluationServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    run = result["run"]
    return {
        "run": {
            "id": run.id,
            "suite": run.suite,
            "candidate_version": run.candidate_version,
            "baseline_version": run.baseline_version,
            "status": run.status,
        },
        "aggregate": result["aggregate"],
        "failed": result["failed"],
    }


@router.get("/runs")
async def list_runs(
    suite: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    records = await container.repository.list_evaluation_runs(
        suite=suite, limit=limit
    )
    return [
        {
            "id": r.id,
            "suite": r.suite,
            "candidate_version": r.candidate_version,
            "baseline_version": r.baseline_version,
            "status": r.status,
            "aggregate": r.aggregate,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in records
    ]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    run = await container.repository.get_evaluation_run(run_id)
    gates = await container.repository.list_gate_results(run_id)
    return {
        "id": run.id,
        "suite": run.suite,
        "candidate_version": run.candidate_version,
        "baseline_version": run.baseline_version,
        "status": run.status,
        "results": run.results,
        "aggregate": run.aggregate,
        "error_samples": run.error_samples,
        "gate_results": [
            {
                "gate_id": g.gate_id,
                "decision": g.decision,
                "reason": g.reason,
                "exempted_by": g.exempted_by,
                "exempt_expires_at": (
                    g.exempt_expires_at.isoformat()
                    if g.exempt_expires_at else None
                ),
            }
            for g in gates
        ],
    }


@router.post("/runs/{run_id}:gates")
async def evaluate_gates(
    run_id: str,
    body: dict[str, Any],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    service = _service(container)
    try:
        results = await service.evaluate_gates(
            run_id,
            exempted_by=(
                str(body["exempted_by"]) if body.get("exempted_by") else None
            ),
            exempt_reason=(
                str(body["exempt_reason"]) if body.get("exempt_reason") else None
            ),
        )
    except EvaluationServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {"gate_results": results}


@router.get("/gates")
async def list_gates(
    suite: str | None = Query(default=None),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    records = await container.repository.list_release_gates(suite=suite)
    return [
        {
            "id": r.id,
            "name": r.name,
            "suite": r.suite,
            "thresholds": r.thresholds,
            "relative_regression_limits": r.relative_regression_limits,
            "mandatory": r.mandatory,
            "enabled": r.enabled,
        }
        for r in records
    ]


@router.post("/gates")
async def create_gate(
    body: dict[str, Any],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    gate = await container.repository.create_release_gate(dict(body))
    return {
        "id": gate.id,
        "name": gate.name,
        "suite": gate.suite,
        "thresholds": gate.thresholds,
    }


@router.post("/drift")
async def drift_check(
    body: dict[str, Any],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    service = _service(container)
    return service.online_drift(
        baseline={
            str(k): float(v)
            for k, v in (body.get("baseline") or {}).items()
        },
        current={
            str(k): float(v)
            for k, v in (body.get("current") or {}).items()
        },
    )
