"""Propagation edge human-confirmation endpoints (M2 传播边人工确认)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.propagation import (
    ConfirmPropagationEdgeRequest,
    PropagationEdgeResponse,
)

router = APIRouter()


@router.get(
    "/{case_id}/propagation-edges",
    response_model=list[PropagationEdgeResponse],
)
async def list_propagation_edges(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[PropagationEdgeResponse]:
    """Return persisted edges with human-confirmation state so the frontend
    can restore confirmed/rejected badges after a reload (BUG-3)."""
    records = await container.repository.list_propagation_edges_by_case(case_id)
    return [PropagationEdgeResponse.model_validate(r) for r in records]


@router.post(
    "/{case_id}/propagation-edges/{edge_id}/confirmation",
    response_model=PropagationEdgeResponse,
)
async def confirm_propagation_edge(
    case_id: str,
    edge_id: str,
    request: ConfirmPropagationEdgeRequest,
    container: ApplicationContainer = Depends(get_container),
) -> PropagationEdgeResponse:
    record = await container.repository.confirm_propagation_edge(
        case_id,
        edge_id,
        confirmed=request.confirmed,
        note=request.note,
    )
    return PropagationEdgeResponse.model_validate(record)
