"""M3: Collection Definition routes（采集定义版本化 API）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ApplicationError
from app.schemas.collections import (
    CollectionDefinitionResponse,
    CreateCollectionDefinitionRequest,
    GenerateCollectionDefinitionRequest,
    ReviseCollectionDefinitionRequest,
)

router = APIRouter()


@router.get("/{case_id}/collection-definitions", response_model=list[CollectionDefinitionResponse])
async def list_collection_definitions(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[CollectionDefinitionResponse]:
    records = await container.collection_service.list_for_case(case_id)
    return [CollectionDefinitionResponse.from_record(record) for record in records]


@router.get(
    "/{case_id}/collection-definitions/active",
    response_model=CollectionDefinitionResponse,
)
async def get_active_collection_definition(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> CollectionDefinitionResponse:
    record = await container.collection_service.get_active(case_id)
    if record is None:
        raise ApplicationError(
            "no active collection definition", code="collection_not_found"
        )
    return CollectionDefinitionResponse.from_record(record)


@router.post(
    "/{case_id}/collection-definitions",
    response_model=CollectionDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_collection_definition(
    case_id: str,
    request: CreateCollectionDefinitionRequest,
    container: ApplicationContainer = Depends(get_container),
) -> CollectionDefinitionResponse:
    record = await container.collection_service.create_manual(
        case_id,
        goal=request.goal,
        platforms=request.platforms,
        platform_queries=request.platform_queries,
        exclusions=request.exclusions,
        filters=request.filters,
    )
    return CollectionDefinitionResponse.from_record(record)


@router.post(
    "/{case_id}/collection-definitions:generate",
    response_model=CollectionDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_collection_definition(
    case_id: str,
    request: GenerateCollectionDefinitionRequest,
    container: ApplicationContainer = Depends(get_container),
) -> CollectionDefinitionResponse:
    record = await container.collection_service.generate(
        case_id, goal=request.goal
    )
    return CollectionDefinitionResponse.from_record(record)


@router.post(
    "/{case_id}/collection-definitions/{definition_id}:revise",
    response_model=CollectionDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def revise_collection_definition(
    case_id: str,
    definition_id: str,
    request: ReviseCollectionDefinitionRequest,
    container: ApplicationContainer = Depends(get_container),
) -> CollectionDefinitionResponse:
    record = await container.collection_service.revise(
        case_id,
        definition_id,
        goal=request.goal,
        platforms=request.platforms,
        platform_queries=request.platform_queries,
        exclusions=request.exclusions,
        filters=request.filters,
    )
    return CollectionDefinitionResponse.from_record(record)


@router.post(
    "/{case_id}/collection-definitions/{definition_id}:activate",
    response_model=CollectionDefinitionResponse,
)
async def activate_collection_definition(
    case_id: str,
    definition_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> CollectionDefinitionResponse:
    record = await container.collection_service.activate(case_id, definition_id)
    return CollectionDefinitionResponse.from_record(record)
