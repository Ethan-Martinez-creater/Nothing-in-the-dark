"""V3 §33: Workspace Entity routes.

主 agent 注册方式：
- entities_router → prefix="/intelligence"（GET /entities、GET /entities/{id}）
- case_router     → prefix="/cases"（GET /{case_id}/entities）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.workspace_entities import (
    WorkspaceEntityListResponse,
    WorkspaceEntityProfileResponse,
)

entities_router = APIRouter()
case_router = APIRouter()


@entities_router.get("/entities", response_model=WorkspaceEntityListResponse)
async def list_entities(
    query: str | None = None,
    platform: str | None = None,
    min_investigations: int = 0,
    limit: int = 50,
    offset: int = 0,
    container: ApplicationContainer = Depends(get_container),
) -> WorkspaceEntityListResponse:
    payload = await container.workspace_entities.list_entities(
        query=query,
        platform=platform,
        min_investigations=min_investigations,
        limit=min(limit, 50),
        offset=min(offset, 5000),
    )
    return WorkspaceEntityListResponse.model_validate(payload)


@entities_router.get(
    "/entities/{entity_id}", response_model=WorkspaceEntityProfileResponse
)
async def get_entity_profile(
    entity_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> WorkspaceEntityProfileResponse:
    payload = await container.workspace_entities.get_profile(entity_id)
    return WorkspaceEntityProfileResponse.model_validate(payload)


@case_router.get("/{case_id}/entities", response_model=WorkspaceEntityListResponse)
async def list_case_entities(
    case_id: str,
    query: str | None = None,
    platform: str | None = None,
    limit: int = 50,
    offset: int = 0,
    container: ApplicationContainer = Depends(get_container),
) -> WorkspaceEntityListResponse:
    """当前 Case 直接出现的 entities（§33；不做全 Workspace dump）。"""
    records = await container.workspace_entities.list_case_entities(
        case_id, query=query, platform=platform, limit=min(limit, 50), offset=offset
    )
    return WorkspaceEntityListResponse.model_validate(records)
