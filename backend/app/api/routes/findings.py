"""M4: Finding routes（结论工作区 API）。

状态机：candidate/under_review/verified/rejected/superseded；
verified/rejected 由 ReviewService 决策同步产生，不提供直接端点。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.findings import (
    AddFindingEvidenceRequest,
    CreateFindingRequest,
    FindingDetailResponse,
    FindingResponse,
    FindingSyncResponse,
    UpdateFindingStatusRequest,
    link_response,
    source_response,
)

router = APIRouter()


@router.get("/{case_id}/findings", response_model=list[FindingResponse])
async def list_findings(
    case_id: str,
    kind: str | None = None,
    status_filter: str | None = None,
    limit: int = 100,
    container: ApplicationContainer = Depends(get_container),
) -> list[FindingResponse]:
    records = await container.finding_service.list(
        case_id, kind=kind, status=status_filter, limit=limit
    )
    return [FindingResponse.from_record(record) for record in records]


@router.get("/{case_id}/findings/{finding_id}", response_model=FindingDetailResponse)
async def get_finding(
    case_id: str,
    finding_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> FindingDetailResponse:
    detail = await container.finding_service.detail(case_id, finding_id)
    record = detail["finding"]
    review = await container.repository.get_review_item_for_object(
        case_id, "finding", record.id
    )
    return FindingDetailResponse(
        finding=FindingResponse.from_record(record),
        evidence_links=[link_response(link) for link in detail["evidence_links"]],
        sources=[source_response(link) for link in detail["sources"]],
        review=review,
    )


@router.post(
    "/{case_id}/findings",
    response_model=FindingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_finding(
    case_id: str,
    request: CreateFindingRequest,
    container: ApplicationContainer = Depends(get_container),
) -> FindingResponse:
    record = await container.finding_service.create_manual(
        case_id,
        kind=request.kind,
        title=request.title,
        statement=request.statement,
        confidence=request.confidence,
        source_type=request.source_type,
        source_id=request.source_id,
        source_path=request.source_path,
    )
    return FindingResponse.from_record(record)


@router.post(
    "/{case_id}/findings:sync",
    response_model=FindingSyncResponse,
)
async def sync_findings(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> FindingSyncResponse:
    result = await container.finding_service.sync_case_history(case_id)
    return FindingSyncResponse(**result)


@router.post(
    "/{case_id}/findings/{finding_id}/status",
    response_model=FindingResponse,
)
async def update_finding_status(
    case_id: str,
    finding_id: str,
    request: UpdateFindingStatusRequest,
    container: ApplicationContainer = Depends(get_container),
) -> FindingResponse:
    """candidate→under_review 等过渡；verified/rejected 走 ReviewService。"""
    record = await container.finding_service.update_status(
        case_id, finding_id, request.status
    )
    return FindingResponse.from_record(record)


@router.post("/{case_id}/findings/{finding_id}/evidence", response_model=FindingResponse)
async def add_finding_evidence(
    case_id: str,
    finding_id: str,
    request: AddFindingEvidenceRequest,
    container: ApplicationContainer = Depends(get_container),
) -> FindingResponse:
    record = await container.finding_service.add_evidence_link(
        case_id, finding_id, request.evidence_ref, request.relation
    )
    return FindingResponse.from_record(record)


@router.delete("/{case_id}/findings/{finding_id}/evidence", response_model=FindingResponse)
async def remove_finding_evidence(
    case_id: str,
    finding_id: str,
    evidence_ref: str,
    relation: str,
    container: ApplicationContainer = Depends(get_container),
) -> FindingResponse:
    await container.finding_service.remove_evidence_link(
        case_id, finding_id, evidence_ref, relation
    )
    record = await container.finding_service.get_for_case(case_id, finding_id)
    return FindingResponse.from_record(record)
