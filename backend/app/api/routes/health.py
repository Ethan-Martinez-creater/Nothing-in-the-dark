from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, object]:
    return {
        "status": "healthy",
        "service": "coifesp-agent-api",
        "timestamp": datetime.now(UTC).isoformat(),
    }

