"""M4: Finding API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.infrastructure.database.models import (
    FindingEvidenceLinkRecord,
    FindingRecord,
    FindingSourceLinkRecord,
)


class CreateFindingRequest(BaseModel):
    kind: str = Field(default="manual", max_length=32)
    title: str | None = Field(default=None, max_length=200)
    statement: str = Field(min_length=1, max_length=8000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_type: str | None = Field(default=None, max_length=32)
    source_id: str | None = Field(default=None, max_length=200)
    source_path: str = Field(default="", max_length=200)


class UpdateFindingStatusRequest(BaseModel):
    """普通 API 可设置的目标状态；verified/rejected 仅经 Review 决策产生。"""

    status: Literal["candidate", "under_review", "superseded"]


class AddFindingEvidenceRequest(BaseModel):
    evidence_ref: str = Field(min_length=1, max_length=200)
    relation: str = Field(pattern="^(supports|contradicts|context)$")


class FindingSourceResponse(BaseModel):
    source_type: str
    source_id: str
    source_path: str

    model_config = {"from_attributes": True}


class FindingEvidenceLinkResponse(BaseModel):
    evidence_ref: str
    relation: str

    model_config = {"from_attributes": True}


class FindingResponse(BaseModel):
    id: str
    case_id: str
    kind: str
    title: str
    statement: str
    status: str
    confidence: float | None
    attributes: dict[str, Any]
    source_run_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_record(cls, record: FindingRecord) -> FindingResponse:
        return cls(
            id=record.id,
            case_id=record.case_id,
            kind=record.kind,
            title=record.title,
            statement=record.statement,
            status=record.status,
            confidence=record.confidence,
            attributes=dict(record.attributes_json or {}),
            source_run_id=record.source_run_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class FindingDetailResponse(BaseModel):
    finding: FindingResponse
    evidence_links: list[FindingEvidenceLinkResponse]
    sources: list[FindingSourceResponse]
    review: dict[str, Any] | None = None


class FindingSyncResponse(BaseModel):
    created: int
    skipped: int
    unsupported: int
    errors: list[dict[str, Any]] = Field(default_factory=list)


def link_response(link: FindingEvidenceLinkRecord) -> FindingEvidenceLinkResponse:
    return FindingEvidenceLinkResponse(evidence_ref=link.evidence_ref, relation=link.relation)


def source_response(link: FindingSourceLinkRecord) -> FindingSourceResponse:
    return FindingSourceResponse(
        source_type=link.source_type,
        source_id=link.source_id,
        source_path=link.source_path,
    )
