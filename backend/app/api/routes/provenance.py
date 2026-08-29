"""M4: Provenance routes（一跳上下游，case scope 强制）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.provenance import ProvenanceResponse

router = APIRouter()


@router.get(
    "/{case_id}/provenance/{object_type}/{object_id}",
    response_model=ProvenanceResponse,
)
async def get_provenance(
    case_id: str,
    object_type: str,
    object_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> ProvenanceResponse:
    result = await container.provenance_service.one_hop(case_id, object_type, object_id)
    return ProvenanceResponse(**result)
