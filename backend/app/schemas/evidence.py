"""Evidence summary schemas (案例证据汇总侧栏)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EvidenceItemResponse(BaseModel):
    id: str
    case_id: str
    claim_id: str | None
    source_type: str
    source_id: str
    stance: str
    excerpt: str
    relevance: float
    metadata_json: dict[str, object] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class ClaimEvidenceResponse(BaseModel):
    id: str
    text: str
    status: str
    verdict: str | None
    confidence: float
    created_at: datetime
    evidence: list[EvidenceItemResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class EvidenceSummaryResponse(BaseModel):
    case_id: str
    claims: list[ClaimEvidenceResponse] = Field(default_factory=list)
    unassigned: list[EvidenceItemResponse] = Field(default_factory=list)


class ReviewClaimRequest(BaseModel):
    confirmed: bool
    note: str | None = Field(default=None, max_length=500)
