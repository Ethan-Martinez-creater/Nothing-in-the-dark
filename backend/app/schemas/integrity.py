"""Integrity risk API contracts (07)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class RiskAssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    subject_type: str
    subject_id: str
    risk_type: str
    score: float
    band: str
    reason_codes: list[str]
    evidence_refs: dict[str, Any]
    model_version: str
    status: str
    reviewed_by: str | None
    review_note: str
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReviewRiskRequest(BaseModel):
    status: Literal["reviewed_likely", "reviewed_unlikely", "inconclusive"]
    by: str | None = None
    note: str = ""


class CoordinationMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    cluster_id: str
    account_id: str
    membership_score: float
    role: str
    evidence: dict[str, Any]
    created_at: datetime


class CoordinationClusterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    window_start: datetime | None
    window_end: datetime | None
    algorithm_version: str
    size: int
    score: float
    explanation: str
    review_status: str
    created_at: datetime


class AnalyzeIntegrityResponse(BaseModel):
    assessments: int
    clusters: int
