"""V3 §43: Cross-case routes.

主 agent 注册方式：
- intelligence_router → prefix="/intelligence"
    GET /connections、GET /connections/{left}/{right}
- case_router → prefix="/cases"
    GET /{case_id}/related-investigations
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
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
