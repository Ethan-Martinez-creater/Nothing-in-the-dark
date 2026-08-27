"""Integrity risk endpoints (07)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.integrity import (
    CoordinationClusterResponse,
    CoordinationMemberResponse,
    ReviewRiskRequest,
    RiskAssessmentResponse,
)

router = APIRouter()


@router.get(
    "/{case_id}/integrity/assessments",
    response_model=list[RiskAssessmentResponse],
)
async def list_assessments(
    case_id: str,
    risk_type: str | None = Query(default=None),
    band: str | None = Query(default=None),
    container: ApplicationContainer = Depends(get_container),
) -> list[RiskAssessmentResponse]:
    await container.repository.get_case(case_id)
    records = await container.integrity_repository.list_assessments(
        case_id, risk_type=risk_type, band=band
    )
    return [RiskAssessmentResponse.model_validate(r) for r in records]


@router.get("/{case_id}/integrity/views")
async def integrity_views(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict:
    """原始/降权/排除三套关键指标与差异。"""
    await container.repository.get_case(case_id)
    return await container.integrity_service.compute_views(case_id)


@router.post("/{case_id}/integrity:analyze", status_code=202)
async def analyze_integrity(
    case_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    container: ApplicationContainer = Depends(get_container),
) -> dict:
    """异步创建完整性分析任务，由 AnalysisJobWorker 领取执行。"""
    await container.repository.get_case(case_id)
    job = await container.analysis_job_repository.create_job(
        case_id=case_id,
        job_type="integrity",
        idempotency_key=idempotency_key,
    )
    return {"job_id": job.id, "status": job.status}


@router.post(
    "/{case_id}/integrity/assessments/{assessment_id}:review",
    response_model=RiskAssessmentResponse,
)
async def review_assessment(
    case_id: str,
    assessment_id: str,
    request: ReviewRiskRequest,
    container: ApplicationContainer = Depends(get_container),
) -> RiskAssessmentResponse:
    record = await container.integrity_repository.get_assessment(assessment_id)
    if record.case_id != case_id:
        from app.core.errors import ApplicationError

        raise ApplicationError("风险评估不属于该案件", code="integrity_scope_mismatch")
    previous = record.status
    updated = await container.integrity_repository.review_assessment(
        assessment_id,
        request.status,
        by=request.by,
        note=request.note,
    )
    await container.repository.create_evaluation(
        case_id=case_id,
        run_id=None,
        metric="integrity_review",
        score=1.0 if request.status == "reviewed_likely" else 0.0,
        details={
            "assessment_id": assessment_id,
            "previous_status": previous,
            "status": request.status,
            "reviewer": request.by or "user",
            "note": request.note,
        },
    )
    return RiskAssessmentResponse.model_validate(updated)


@router.get(
    "/{case_id}/integrity/clusters",
    response_model=list[CoordinationClusterResponse],
)
async def list_clusters(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[CoordinationClusterResponse]:
    await container.repository.get_case(case_id)
    records = await container.integrity_repository.list_clusters(case_id)
    return [CoordinationClusterResponse.model_validate(r) for r in records]


@router.get(
    "/{case_id}/integrity/clusters/{cluster_id}",
    response_model=CoordinationClusterResponse,
)
async def get_cluster(
    case_id: str,
    cluster_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> CoordinationClusterResponse:
    cluster = await container.integrity_repository.get_cluster(cluster_id)
    if cluster.case_id != case_id:
        from app.core.errors import ApplicationError

        raise ApplicationError("协同群体不属于该案件", code="integrity_scope_mismatch")
    return CoordinationClusterResponse.model_validate(cluster)


@router.get(
    "/{case_id}/integrity/clusters/{cluster_id}/members",
    response_model=list[CoordinationMemberResponse],
)
async def list_cluster_members(
    case_id: str,
    cluster_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[CoordinationMemberResponse]:
    cluster = await container.integrity_repository.get_cluster(cluster_id)
    if cluster.case_id != case_id:
        from app.core.errors import ApplicationError

        raise ApplicationError("协同群体不属于该案件", code="integrity_scope_mismatch")
    members = await container.integrity_repository.list_cluster_members(cluster_id)
    return [CoordinationMemberResponse.model_validate(m) for m in members]
