"""Uncertainty & bias endpoints (08)."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.uncertainty import (
    CombineConfidenceRequest,
    CombineConfidenceResponse,
    ConclusionConfidenceResponse,
    HypothesisCreateRequest,
    HypothesisResponse,
    QualityAssessmentResponse,
    QualitySummaryResponse,
    SensitivityRunRequest,
    SensitivityRunResponse,
)
from app.services import uncertainty

router = APIRouter()


@router.get("/{case_id}/quality/summary", response_model=QualitySummaryResponse)
async def quality_summary(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> QualitySummaryResponse:
    await container.repository.get_case(case_id)
    assessments = await container.uncertainty_repository.list_quality_assessments(case_id)
    conclusions = await container.uncertainty_repository.list_conclusions(case_id)
    return QualitySummaryResponse(
        case_id=case_id,
        assessments=[QualityAssessmentResponse.model_validate(a) for a in assessments],
        conclusions=[ConclusionConfidenceResponse.model_validate(c) for c in conclusions],
    )


@router.post("/{case_id}/quality/combine", response_model=CombineConfidenceResponse)
async def combine_confidence(
    case_id: str,
    request: CombineConfidenceRequest,
    container: ApplicationContainer = Depends(get_container),
) -> CombineConfidenceResponse:
    await container.repository.get_case(case_id)
    level, reasons = uncertainty.combine_confidence(request.dimensions)
    return CombineConfidenceResponse(final_level=level, forbidden_reasons=reasons)


@router.post("/{case_id}/hypotheses", response_model=HypothesisResponse, status_code=201)
async def create_hypothesis(
    case_id: str,
    request: HypothesisCreateRequest,
    container: ApplicationContainer = Depends(get_container),
) -> HypothesisResponse:
    await container.repository.get_case(case_id)
    record = await container.uncertainty_repository.create_hypothesis(
        case_id=case_id,
        statement=request.statement,
        prediction=request.prediction,
        supporting_evidence=request.supporting_evidence,
        opposing_evidence=request.opposing_evidence,
        proposer=request.proposer,
    )
    return HypothesisResponse.model_validate(record)


@router.get("/{case_id}/hypotheses", response_model=list[HypothesisResponse])
async def list_hypotheses(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[HypothesisResponse]:
    await container.repository.get_case(case_id)
    records = await container.uncertainty_repository.list_hypotheses(case_id)
    return [HypothesisResponse.model_validate(r) for r in records]


@router.post("/{case_id}/sensitivity-runs", response_model=SensitivityRunResponse)
async def create_sensitivity_run(
    case_id: str,
    request: SensitivityRunRequest,
    container: ApplicationContainer = Depends(get_container),
) -> SensitivityRunResponse:
    await container.repository.get_case(case_id)
    baseline_hash = hashlib.sha256(
        json.dumps(request.baseline_params, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]
    diff = uncertainty.sensitivity_difference(
        request.baseline_params, request.variant_params
    )
    record = await container.uncertainty_repository.create_sensitivity_run(
        case_id=case_id,
        baseline_hash=baseline_hash,
        baseline_params=request.baseline_params,
        variant_params=request.variant_params,
        output_diff=diff,
    )
    if record is None:
        from app.core.errors import ApplicationError

        raise ApplicationError("相同参数的敏感性运行已存在", code="sensitivity_run_duplicate")
    return SensitivityRunResponse.model_validate(record)


@router.get("/{case_id}/sensitivity-runs/{run_id}", response_model=SensitivityRunResponse)
async def get_sensitivity_run(
    case_id: str,
    run_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> SensitivityRunResponse:
    record = await container.uncertainty_repository.get_sensitivity_run(run_id)
    if record.case_id != case_id:
        from app.core.errors import ApplicationError

        raise ApplicationError("敏感性运行不属于该案件", code="uncertainty_scope_mismatch")
    return SensitivityRunResponse.model_validate(record)
