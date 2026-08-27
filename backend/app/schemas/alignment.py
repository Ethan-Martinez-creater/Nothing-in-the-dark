"""Cross-platform alignment API contracts (06)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AlignmentCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    left_type: str
    left_id: str
    right_type: str
    right_id: str
    relation_type: str
    feature_scores: dict[str, Any]
    combined_score: float
    decision: str
    review_id: str | None
    model_version: str
    created_at: datetime
    updated_at: datetime


class ReviewCandidateRequest(BaseModel):
    note: str | None = None


class CanonicalEntityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    entity_type: str
    canonical_name: str
    aliases: list[str]
    description: str
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class ContentFamilyMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    family_id: str
    member_type: str
    member_id: str
    relation: str
    time_offset_ms: int | None
    edit_features: dict[str, Any]
    decision_source: str
    created_at: datetime


class ContentFamilyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    label: str
    earliest_known_id: str | None
    summary: str
    status: str
    created_at: datetime
    updated_at: datetime


class AnalyzeResponse(BaseModel):
    content_candidates: int
    account_candidates: int
