"""Platform auth (QR login + credential management) API.

所有响应只包含平台状态元数据与二维码 data URL，绝不返回 cookie 明文、
nonce、ciphertext 或 master key。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.services.platform_auth import (
    SESSION_STARTING,
    SESSION_WAITING_SCAN,
)

router = APIRouter()

_QR_VISIBLE_STATUSES = frozenset({SESSION_STARTING, SESSION_WAITING_SCAN})


@router.get("")
async def list_platform_auth(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    return {"items": await container.platform_auth_service.list_status()}


@router.post("/{platform}/login-sessions")
async def create_login_session(
    platform: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    session = await container.login_session_coordinator.start(platform)
    return {
        "session_id": session.id,
        "platform": session.platform,
        "status": session.status,
        "expires_at": session.expires_at.isoformat(),
    }


@router.get("/login-sessions/{session_id}")
async def get_login_session(
    session_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    session = await container.login_session_coordinator.get(session_id)
    return {
        "session_id": session.id,
        "platform": session.platform,
        "status": session.status,
        # 终态（authenticated/failed/expired/cancelled）后不再返回二维码。
        "qr_code": (
            session.qr_code if session.status in _QR_VISIBLE_STATUSES else None
        ),
        "expires_at": session.expires_at.isoformat(),
        "error": session.error_message,
    }


@router.delete("/login-sessions/{session_id}")
async def cancel_login_session(
    session_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    await container.login_session_coordinator.cancel(session_id)
    return {"ok": True}


@router.post("/{platform}/validate")
async def validate_platform(
    platform: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    return await container.platform_auth_service.validate(platform)


@router.delete("/{platform}")
async def revoke_platform(
    platform: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    deleted = await container.platform_auth_service.revoke(platform)
    return {"ok": deleted}
