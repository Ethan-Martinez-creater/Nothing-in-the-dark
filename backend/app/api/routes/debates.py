"""Debate endpoints (多角色辩论验证)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.application.repositories import ApplicationRepository
from app.bootstrap import ApplicationContainer
from app.schemas.debates import (
    CreateDebateRequest,
    DebateDetailResponse,
    DebateMessageResponse,
    DebateResponse,
    DebateVoteResponse,
    UserMessageRequest,
)

router = APIRouter()


async def build_detail(
    repository: ApplicationRepository, debate_id: str
) -> DebateDetailResponse:
    debate = await repository.get_debate(debate_id)
    messages = await repository.list_debate_messages(debate_id)
    votes = await repository.list_debate_votes(debate_id)
    return DebateDetailResponse(
        **DebateResponse.model_validate(debate).model_dump(),
        messages=[
            DebateMessageResponse.model_validate(m) for m in messages
        ],
        votes=[DebateVoteResponse.model_validate(v) for v in votes],
    )


@router.post(
    "/{case_id}/debates",
    response_model=DebateResponse,
    status_code=201,
)
async def create_debate(
    case_id: str,
    request: CreateDebateRequest,
    container: ApplicationContainer = Depends(get_container),
) -> DebateResponse:
    debate = await container.debate_service.create_debate(
        case_id, request.title
    )
    return DebateResponse.model_validate(debate)


@router.get("/{case_id}/debates", response_model=list[DebateResponse])
async def list_debates(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[DebateResponse]:
    records = await container.repository.list_debates(case_id)
    return [DebateResponse.model_validate(r) for r in records]


@router.get("/debates/{debate_id}", response_model=DebateDetailResponse)
async def get_debate(
    debate_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> DebateDetailResponse:
    return await build_detail(container.repository, debate_id)


@router.post("/debates/{debate_id}/messages", response_model=DebateMessageResponse)
async def add_user_message(
    debate_id: str,
    request: UserMessageRequest,
    container: ApplicationContainer = Depends(get_container),
) -> DebateMessageResponse:
    message = await container.debate_service.add_user_message(
        debate_id, request.content
    )
    return DebateMessageResponse.model_validate(message)


@router.post("/debates/{debate_id}/advance", response_model=DebateDetailResponse)
async def advance_debate(
    debate_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> DebateDetailResponse:
    await container.debate_service.advance(debate_id)
    return await build_detail(container.repository, debate_id)


@router.get("/debates/{debate_id}/votes", response_model=list[DebateVoteResponse])
async def list_votes(
    debate_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[DebateVoteResponse]:
    records = await container.repository.list_debate_votes(debate_id)
    return [DebateVoteResponse.model_validate(r) for r in records]
