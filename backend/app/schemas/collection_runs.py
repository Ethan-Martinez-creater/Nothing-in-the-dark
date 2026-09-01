"""CollectionRun API schemas（async progressive collection）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class CollectionRunPlatformState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    attempts: int = 0
    posts_collected: int = 0
    comments_collected: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class CollectionRunResponse(BaseModel):
    id: str
    case_id: str
    phase: str
    status: str
    posts_collected: int
    comments_collected: int
    collection_definition_id: str | None = None
    collection_definition_version: int | None = None
    trigger_run_id: str | None = None
    trigger_tool_call_id: str | None = None
    approval_id: str | None = None
    platforms: list[str] = []
    platform_progress: dict[str, CollectionRunPlatformState] = {}
    error_code: str | None = None
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str
    updated_at: str

    @classmethod
    def from_record(cls, record: Any) -> "CollectionRunResponse":
        progress = dict(record.progress_json or {})
        platforms_raw = progress.get("platforms") or {}
        platform_progress: dict[str, CollectionRunPlatformState] = {}
        for platform, state in platforms_raw.items():
            platform_progress[str(platform)] = CollectionRunPlatformState(
                **(
                    dict(state)
                    if isinstance(state, dict)
                    else {"status": "queued"}
                )
            )
        return cls(
            id=record.id,
            case_id=record.case_id,
            phase=record.phase,
            status=record.status,
            posts_collected=record.posts_collected,
            comments_collected=record.comments_collected,
            collection_definition_id=record.collection_definition_id,
            collection_definition_version=record.collection_definition_version,
            trigger_run_id=record.trigger_run_id,
            trigger_tool_call_id=record.trigger_tool_call_id,
            approval_id=record.approval_id,
            platforms=list((record.request_json or {}).get("platforms") or []),
            platform_progress=platform_progress,
            error_code=record.error_code,
            error_message=record.error_message,
            started_at=record.started_at.isoformat() if record.started_at else None,
            completed_at=(
                record.completed_at.isoformat() if record.completed_at else None
            ),
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
        )
