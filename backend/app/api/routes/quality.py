"""V3 §23: Investigation Quality routes.

主 agent 以 ``api_router.include_router(quality.router, prefix="/cases",
tags=["quality"])`` 注册：
- GET  /api/v1/cases/{case_id}/quality           （fresh-if-needed）
- POST /api/v1/cases/{case_id}/quality:refresh   （force recompute）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.quality import (
    HomeQualityAttentionItem,
    InvestigationQualityResponse,
)

router = APIRouter()


@router.get("/{case_id}/quality", response_model=InvestigationQualityResponse)
async def get_quality(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> InvestigationQualityResponse:
    payload = await container.investigation_quality.evaluate(case_id)
    return InvestigationQualityResponse.model_validate(payload)


@router.post(
    "/{case_id}/quality:refresh", response_model=InvestigationQualityResponse
)
async def refresh_quality(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> InvestigationQualityResponse:
    payload = await container.investigation_quality.evaluate(case_id, force=True)
    return InvestigationQualityResponse.model_validate(payload)


attention_router = APIRouter()


@attention_router.get(
    "/quality/needs-attention",
    response_model=list[HomeQualityAttentionItem],
)
async def list_quality_needing_attention(
    limit: int = 5,
    container: ApplicationContainer = Depends(get_container),
) -> list[HomeQualityAttentionItem]:
    """预期以 prefix="" 注册（/api/v1/quality/needs-attention），供 Home 聚合。"""
    entries = await container.investigation_quality.list_needing_attention(
        limit=limit
    )
    return [HomeQualityAttentionItem.model_validate(entry) for entry in entries]
