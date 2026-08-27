"""Project endpoints (侧边栏项目分组)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.projects import (
    CreateProjectRequest,
    ProjectResponse,
    RenameProjectRequest,
)

router = APIRouter()


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    request: CreateProjectRequest,
    container: ApplicationContainer = Depends(get_container),
) -> ProjectResponse:
    record = await container.repository.create_project(request.title)
    return ProjectResponse.model_validate(record)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    container: ApplicationContainer = Depends(get_container),
) -> list[ProjectResponse]:
    records = await container.repository.list_projects()
    return [ProjectResponse.model_validate(r) for r in records]


@router.patch("/{project_id}", response_model=ProjectResponse)
async def rename_project(
    project_id: str,
    request: RenameProjectRequest,
    container: ApplicationContainer = Depends(get_container),
) -> ProjectResponse:
    record = await container.repository.rename_project(project_id, request.title)
    return ProjectResponse.model_validate(record)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> None:
    await container.repository.delete_project(project_id)
