"""M6: Global Signals routes（Monitor Alert adapter，不复制状态机）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.signals import SignalResponse

router = APIRouter()


@router.get("", response_model=list[SignalResponse])
async def list_signals(
    status: str | None = None,
    severity: str | None = None,
    case_id: str | None = None,
    signal_type: str | None = None,
    source_type: str | None = None,
    detector_active: bool | None = None,
    limit: int = 100,
    container: ApplicationContainer = Depends(get_container),
) -> list[SignalResponse]:
    # 默认 open + acknowledged（Signal Inbox 视角）；显式传 status 时以传入为准。
    statuses = status.split(",") if status else ["open", "acknowledged"]
    return await container.signal_service.list_signals(
        statuses=statuses,
        severity=severity,
        case_id=case_id,
        signal_type=signal_type,
        source_type=source_type,
        detector_active=detector_active,
        limit=limit,
    )


@router.get("/{signal_id}", response_model=SignalResponse)
async def get_signal(
    signal_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> SignalResponse:
    return await container.signal_service.get_signal(signal_id)


@router.post("/{signal_id}:acknowledge", response_model=SignalResponse)
async def acknowledge_signal(
    signal_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> SignalResponse:
    return await container.signal_service.change_status(signal_id, "acknowledge")


@router.post("/{signal_id}:resolve", response_model=SignalResponse)
async def resolve_signal(
    signal_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> SignalResponse:
    return await container.signal_service.change_status(signal_id, "resolve")


@router.post("/{signal_id}:suppress", response_model=SignalResponse)
async def suppress_signal(
    signal_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> SignalResponse:
    return await container.signal_service.change_status(signal_id, "suppress")
