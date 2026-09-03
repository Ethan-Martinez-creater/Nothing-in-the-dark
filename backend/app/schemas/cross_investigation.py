"""V3 §42/§43: Cross-Investigation DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RelatedInvestigationResponse(BaseModel):
    case_id: str
    title: str
    relation_types: list[str]
    relation_count: int
    max_score: float
    shared_actor_count: int = 0
    shared_post_count: int = 0
    shared_media_count: int = 0
    shared_content_count: int = 0
    has_candidate_relation: bool = False


class CrossLinkResponse(BaseModel):
    id: str
    left_case_id: str
    right_case_id: str
    left_title: str | None = None
    right_title: str | None = None
    relation_type: str
    status: str
    score: float | None = None
    evidence_count: int = 0
    algorithm_version: str


class CrossConnectionDetailResponse(BaseModel):
    links: list[CrossLinkResponse]
    left_case_id: str
    right_case_id: str


class CrossLinkRecordResponse(BaseModel):
    id: str
    relation_type: str
    status: str
    score: float | None = None
    evidence_count: int = 0
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    feature_scores: dict[str, Any] = Field(default_factory=dict)
    algorithm_version: str
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
