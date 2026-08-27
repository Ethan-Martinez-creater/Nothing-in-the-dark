"""Cross-platform alignment endpoints (06)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ApplicationError
from app.schemas.alignment import (
    AlignmentCandidateResponse,
    CanonicalEntityResponse,
    ContentFamilyMemberResponse,
    ContentFamilyResponse,
    ReviewCandidateRequest,
)

router = APIRouter()


@router.get(
    "/{case_id}/alignments/candidates",
    response_model=list[AlignmentCandidateResponse],
)
async def list_alignment_candidates(
    case_id: str,
    decision: str | None = Query(default=None),
    relation_type: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    container: ApplicationContainer = Depends(get_container),
) -> list[AlignmentCandidateResponse]:
    await container.repository.get_case(case_id)
    records = await container.alignment_repository.list_candidates(
        case_id,
        decision=decision,
        relation_type=relation_type,
        limit=limit,
    )
    return [AlignmentCandidateResponse.model_validate(r) for r in records]


@router.post("/{case_id}/alignments:analyze", status_code=202)
async def analyze_alignments(
    case_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    container: ApplicationContainer = Depends(get_container),
) -> dict:
    """异步创建对齐分析任务，由 AnalysisJobWorker 领取执行。"""
    await container.repository.get_case(case_id)
    job = await container.analysis_job_repository.create_job(
        case_id=case_id,
        job_type="alignment",
        idempotency_key=idempotency_key,
    )
    return {"job_id": job.id, "status": job.status}


async def _set_candidate(
    container: ApplicationContainer,
    case_id: str,
    candidate_id: str,
    decision: str,
    note: str | None = None,
) -> AlignmentCandidateResponse:
    candidate = await container.alignment_repository.get_candidate(candidate_id)
    if candidate.case_id != case_id:
        raise ApplicationError("对齐候选不属于该案件", code="alignment_scope_mismatch")
    previous = candidate.decision
    if previous == "confirmed" and decision != "confirmed":
        await container.alignment_service.retract_candidate(case_id, candidate_id)
    updated = await container.alignment_repository.set_candidate_decision(
        candidate_id, decision
    )
    # 确认时生成规范实体/内容族；撤销只删除候选产生的关系，不删原对象。
    if decision == "confirmed":
        await container.alignment_service.materialize_candidate(case_id, candidate_id)
    # 审核审计：append-only evaluation 记录（含操作人/前后值/原因）。
    await container.repository.create_evaluation(
        case_id=case_id,
        run_id=None,
        metric="alignment_review",
        score=1.0 if decision == "confirmed" else 0.0,
        details={
            "candidate_id": candidate_id,
            "decision": decision,
            "previous_decision": previous,
            "note": note or "",
            "reviewer": "user",
        },
    )
    return AlignmentCandidateResponse.model_validate(updated)


@router.post(
    "/{case_id}/alignments/{candidate_id}:confirm",
    response_model=AlignmentCandidateResponse,
)
async def confirm_candidate(
    case_id: str,
    candidate_id: str,
    request: ReviewCandidateRequest,
    container: ApplicationContainer = Depends(get_container),
) -> AlignmentCandidateResponse:
    return await _set_candidate(
        container, case_id, candidate_id, "confirmed", request.note
    )


@router.post(
    "/{case_id}/alignments/{candidate_id}:reject",
    response_model=AlignmentCandidateResponse,
)
async def reject_candidate(
    case_id: str,
    candidate_id: str,
    request: ReviewCandidateRequest,
    container: ApplicationContainer = Depends(get_container),
) -> AlignmentCandidateResponse:
    return await _set_candidate(
        container, case_id, candidate_id, "rejected", request.note
    )


@router.post(
    "/{case_id}/alignments/{candidate_id}:reopen",
    response_model=AlignmentCandidateResponse,
)
async def reopen_candidate(
    case_id: str,
    candidate_id: str,
    request: ReviewCandidateRequest,
    container: ApplicationContainer = Depends(get_container),
) -> AlignmentCandidateResponse:
    return await _set_candidate(
        container, case_id, candidate_id, "pending", request.note
    )


@router.get("/{case_id}/entities/{entity_id}", response_model=CanonicalEntityResponse)
async def get_entity(
    case_id: str,
    entity_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> CanonicalEntityResponse:
    entity = await container.alignment_repository.get_entity(entity_id)
    if entity.case_id != case_id:
        raise ApplicationError("实体不属于该案件", code="entity_scope_mismatch")
    return CanonicalEntityResponse.model_validate(entity)


@router.get(
    "/{case_id}/content-families/{family_id}",
    response_model=ContentFamilyResponse,
)
async def get_content_family(
    case_id: str,
    family_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> ContentFamilyResponse:
    family = await container.alignment_repository.get_family(family_id)
    if family.case_id != case_id:
        raise ApplicationError("内容族不属于该案件", code="family_scope_mismatch")
    return ContentFamilyResponse.model_validate(family)


@router.get(
    "/{case_id}/content-families/{family_id}/members",
    response_model=list[ContentFamilyMemberResponse],
)
async def list_content_family_members(
    case_id: str,
    family_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[ContentFamilyMemberResponse]:
    family = await container.alignment_repository.get_family(family_id)
    if family.case_id != case_id:
        raise ApplicationError("内容族不属于该案件", code="family_scope_mismatch")
    members = await container.alignment_repository.list_family_members(family_id)
    return [ContentFamilyMemberResponse.model_validate(m) for m in members]
