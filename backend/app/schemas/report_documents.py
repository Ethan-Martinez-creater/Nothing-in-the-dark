"""M7: Report Document API 契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.infrastructure.database.models import ReportDocumentRecord


class ImportReportRequest(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=36)


class UpdateReportRequest(BaseModel):
    expected_lock_version: int
    title: str | None = Field(default=None, max_length=300)
    content: dict[str, Any] | None = None


class ReportDocumentResponse(BaseModel):
    id: str
    family_id: str
    case_id: str
    source_artifact_id: str
    supersedes_id: str | None
    status: str
    title: str
    content_json: dict[str, Any]
    lock_version: int
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_record(cls, record: ReportDocumentRecord) -> ReportDocumentResponse:
        return cls(
            id=record.id,
            family_id=record.family_id,
            case_id=record.case_id,
            source_artifact_id=record.source_artifact_id,
            supersedes_id=record.supersedes_id,
            status=record.status,
            title=record.title,
            content_json=dict(record.content_json or {}),
            lock_version=record.lock_version,
            published_at=record.published_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
