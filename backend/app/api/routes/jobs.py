"""Async analysis job endpoints (A-02)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer


class AnalysisJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    job_type: str
    status: str
    idempotency_key: str | None
    attempt: int
    max_attempts: int
    cancel_requested: bool
    progress_json: dict
    result_json: dict
    error_code: str | None
    created_at: datetime
    updated_at: datetime


router = APIRouter()


@router.get("/{case_id}/jobs", response_model=list[AnalysisJobResponse])
async def list_jobs(
    case_id: str,
    job_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    container: ApplicationContainer = Depends(get_container),
) -> list[AnalysisJobResponse]:
    await container.repository.get_case(case_id)
    records = await container.analysis_job_repository.list_jobs(
        case_id, job_type=job_type, limit=limit
    )
    return [AnalysisJobResponse.model_validate(r) for r in records]


@router.get("/{case_id}/jobs/{job_id}", response_model=AnalysisJobResponse)
async def get_job(
    case_id: str,
    job_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> AnalysisJobResponse:
    job = await container.analysis_job_repository.get_job(job_id)
    if job.case_id != case_id:
        from app.core.errors import ApplicationError

        raise ApplicationError("分析任务不属于该案件", code="job_scope_mismatch")
    return AnalysisJobResponse.model_validate(job)


@router.post("/{case_id}/jobs/{job_id}:cancel", response_model=AnalysisJobResponse)
async def cancel_job(
    case_id: str,
    job_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> AnalysisJobResponse:
    job = await container.analysis_job_repository.get_job(job_id)
    if job.case_id != case_id:
        from app.core.errors import ApplicationError

        raise ApplicationError("分析任务不属于该案件", code="job_scope_mismatch")
    cancelled = await container.analysis_job_repository.request_cancel(job_id)
    return AnalysisJobResponse.model_validate(cancelled)
