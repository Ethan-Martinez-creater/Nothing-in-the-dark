"""Async progressive collection run routes（list / get / cancel，Case scope）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ApplicationError, ResourceNotFoundError
from app.schemas.collection_runs import CollectionRunResponse

router = APIRouter()


@router.get(
    "/{case_id}/collection-runs",
    response_model=list[CollectionRunResponse],
)
async def list_collection_runs(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
    active: bool = Query(default=False),
    status: str | None = Query(default=None),
    phase: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[CollectionRunResponse]:
    records = await container.collection_run_service.list_for_case(
        case_id,
        active_only=active,
        status=status,
        phase=phase,
        limit=limit,
    )
    return [CollectionRunResponse.from_record(record) for record in records]


@router.get(
    "/{case_id}/collection-runs/{run_id}",
    response_model=CollectionRunResponse,
)
async def get_collection_run(
    case_id: str,
    run_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> CollectionRunResponse:
    try:
        record = await container.collection_run_service.get_for_case(
            case_id, run_id
        )
    except ResourceNotFoundError as exc:
        raise ApplicationError(
            "collection run not found", code="collection_run_not_found"
        ) from exc
    return CollectionRunResponse.from_record(record)


@router.post(
    "/{case_id}/collection-runs/{run_id}:cancel",
    response_model=CollectionRunResponse,
)
async def cancel_collection_run(
    case_id: str,
    run_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> CollectionRunResponse:
    record = await container.collection_run_service.cancel(case_id, run_id)
    return CollectionRunResponse.from_record(record)
