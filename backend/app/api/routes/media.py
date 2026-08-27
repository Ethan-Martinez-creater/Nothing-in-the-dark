"""Media pipeline endpoints (04 多模态流水线)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.media import (
    BackfillRequest,
    BackfillResponse,
    MediaAssetDetailResponse,
    MediaAssetResponse,
    MediaTranscriptResponse,
)

router = APIRouter()

_STAGES_BY_TYPE = {
    "image": ("probe", "ocr", "c2pa"),
    "video": ("probe", "asr", "keyframe", "c2pa"),
    "audio": ("probe", "asr", "c2pa"),
}


@router.get("/{case_id}/media", response_model=list[MediaAssetResponse])
async def list_media_assets(
    case_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    container: ApplicationContainer = Depends(get_container),
) -> list[MediaAssetResponse]:
    await container.repository.get_case(case_id)
    records = await container.media_repository.list_assets_by_case(case_id, limit=limit)
    return [MediaAssetResponse.model_validate(r) for r in records]


@router.get("/{case_id}/media/{asset_id}", response_model=MediaAssetDetailResponse)
async def get_media_asset(
    case_id: str,
    asset_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> MediaAssetDetailResponse:
    await container.repository.get_case(case_id)
    asset = await container.media_repository.get_asset(asset_id)
    if asset.case_id != case_id:
        from app.core.errors import ApplicationError

        raise ApplicationError("媒体资产不属于该案件", code="media_scope_mismatch")
    transcripts = await container.media_repository.list_transcripts(asset_id)
    base = MediaAssetResponse.model_validate(asset)
    return MediaAssetDetailResponse(
        **base.model_dump(),
        transcripts=[MediaTranscriptResponse.model_validate(t) for t in transcripts],
        derivatives=[],
    )


@router.post("/{case_id}/media/backfill", response_model=BackfillResponse)
async def backfill_media(
    case_id: str,
    request: BackfillRequest,
    container: ApplicationContainer = Depends(get_container),
) -> BackfillResponse:
    """为未分析媒体资产排入流水线任务（幂等，Worker 异步领取）。"""
    await container.repository.get_case(case_id)
    assets = await container.media_repository.list_assets_by_case(case_id, limit=request.limit)
    enqueued = 0
    for asset in assets:
        if asset.download_status in ("not_downloaded", "failed"):
            if await container.media_repository.create_job(asset.id, "download"):
                enqueued += 1
        elif asset.download_status == "downloaded" and asset.analysis_status in (
            "pending",
            "partial",
        ):
            stages = _STAGES_BY_TYPE.get(asset.media_type, _STAGES_BY_TYPE["image"])
            for stage in stages:
                if await container.media_repository.create_job(asset.id, stage):
                    enqueued += 1
    return BackfillResponse(enqueued=enqueued)
