"""10 叙事生命周期与纠错传播评估 API 契约。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NarrativeResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    case_id: str
    title: str
    canonical_summary: str
    status: str
    created_source: str
    review_state: str
    created_at: datetime


class NarrativeVersionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    narrative_id: str
    data_watermark: datetime | None = None
    algorithm_version: str
    keywords: list[str]
    metrics: dict[str, object]
    created_at: datetime


class CorrectionCreate(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    source_post_id: str | None = None
    claim_id: str | None = None
    target_narrative_id: str | None = None
    correction_type: str = "clarification"
    publisher_class: str = "unknown"


class CorrectionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    case_id: str
    source_post_id: str | None = None
    claim_id: str | None = None
    target_narrative_id: str | None = None
    correction_type: str
    content: str
    publisher_class: str
    review_state: str
    created_at: datetime


class MergeRequest(BaseModel):
    target_narrative_id: str


class SplitRequest(BaseModel):
    title: str = ""
