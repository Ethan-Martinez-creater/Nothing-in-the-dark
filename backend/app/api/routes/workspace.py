"""M6: Workspace Overview route（Home 聚合端点）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.workspace import WorkspaceOverviewResponse

router = APIRouter()


@router.get("/overview", response_model=WorkspaceOverviewResponse)
async def workspace_overview(
    container: ApplicationContainer = Depends(get_container),
) -> WorkspaceOverviewResponse:
    return await container.workspace_service.overview()
