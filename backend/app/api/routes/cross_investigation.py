"""V3 §43: Cross-case routes.

主 agent 注册方式：
- intelligence_router → prefix="/intelligence"
    GET /connections、GET /connections/{left}/{right}
- case_router → prefix="/cases"
    GET /{case_id}/related-investigations
    POST /{case_id}/intelligence:refresh（§64 Manual Refresh）
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ResourceNotFoundError
from app.core.v3 import V3_INTELLIGENCE_VERSION
from app.schemas.cross_investigation import (
    CrossLinkResponse,
    RelatedInvestigationResponse,
)

intelligence_router = APIRouter()
case_router = APIRouter()


@case_router.get(
    "/{case_id}/related-investigations",
    response_model=list[RelatedInvestigationResponse],
)
async def list_related_investigations(
    case_id: str,
    limit: int = 5,
    container: ApplicationContainer = Depends(get_container),
) -> list[RelatedInvestigationResponse]:
    payload = await container.cross_investigation.related_investigations(
        case_id, limit=limit
    )
    return [RelatedInvestigationResponse.model_validate(item) for item in payload]


@case_router.post("/{case_id}/intelligence:refresh")
async def refresh_intelligence(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, str]:
    """§64：完整刷新 V3 Intelligence dependencies（alignment + integrity）。

    完成后由 AnalysisJobWorker follow-up enqueue intelligence_refresh。
    """
    try:
        await container.repository.get_case(case_id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    minute = datetime.now(UTC).strftime("%Y%m%d%H%M")
    alignment_job = await container.analysis_job_repository.create_job(
        case_id=case_id,
        job_type="alignment",
        idempotency_key=(
            f"manual-v3:alignment:{case_id}:{minute}:{V3_INTELLIGENCE_VERSION}"
        ),
    )
    integrity_job = await container.analysis_job_repository.create_job(
        case_id=case_id,
        job_type="integrity",
        idempotency_key=(
            f"manual-v3:integrity:{case_id}:{minute}:{V3_INTELLIGENCE_VERSION}"
        ),
    )
    return {
        "status": "accepted",
        "alignment_job_id": alignment_job.id,
        "integrity_job_id": integrity_job.id,
    }


@intelligence_router.get("/connections", response_model=list[CrossLinkResponse])
async def list_connections(
    relation_type: str | None = None,
    status: str | None = None,
    limit: int = 200,
    container: ApplicationContainer = Depends(get_container),
) -> list[CrossLinkResponse]:
    """默认 active_only（§10.1）；查看历史需显式传 status / 专用参数。"""
    payload = await container.cross_investigation.workspace_connections(
        relation_type=relation_type,
        status=status,
        limit=limit,
    )
    return [CrossLinkResponse.model_validate(item) for item in payload]


@intelligence_router.get(
    "/connections/{left_case_id}/{right_case_id}",
    response_model=list[CrossLinkResponse],
)
async def list_connection_between(
    left_case_id: str,
    right_case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[CrossLinkResponse]:
    from app.core.errors import ResourceNotFoundError

    try:
        left = await container.repository.get_case(left_case_id)
        right = await container.repository.get_case(right_case_id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    links = await container.cross_investigation.list_between(
        left.id, right.id
    )
    return [
        CrossLinkResponse.model_validate(
            {
                "id": link.id,
                "left_case_id": link.left_case_id,
                "right_case_id": link.right_case_id,
                "left_title": left.title,
                "right_title": right.title,
                "relation_type": link.relation_type,
                "status": link.status,
                "score": link.score,
                "evidence_count": link.evidence_count,
                "algorithm_version": link.algorithm_version,
            }
        )
        for link in links
    ]
