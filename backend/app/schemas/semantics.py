"""11 语义分析 API 契约。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LexiconEntryCreate(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    normalized: str = ""
    meaning: str = ""
    domain: str = "general"
    platform: str = ""
    language: str = "zh"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source: str = ""
    review_state: str = "proposed"


class LexiconEntryResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    term: str
    normalized: str
    meaning: str
    domain: str
    platform: str
    language: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source: str
    review_state: str
    version: str
    created_at: datetime


class AnalyzeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    tasks: list[str] = Field(
        default_factory=lambda: ["sentiment", "stance", "irony", "entity"]
    )
    source_type: str = "post"
    source_id: str = ""
    platform: str = ""
    domain: str = ""


class AnalyzeResponse(BaseModel):
    original: str
    normalized: str
    language: dict[str, object]
    lexicon_hits: list[dict[str, object]] = []
    results: list[dict[str, object]]
    fallback: bool
    semantic_version: str


class CorrectionCreate(BaseModel):
    annotation_id: str
    original: dict[str, object]
    corrected: dict[str, object]
    reason: str = ""
    actor: str = "local_operator"


class CorrectionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    annotation_id: str
    original: dict[str, object]
    corrected: dict[str, object]
    reason: str
    actor: str
    created_at: datetime


class ModelVersionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    component: str
    version: str
    capability: str
    training_data_version: str
    eval_data_version: str
    thresholds: dict[str, object]
    status: str
    created_at: datetime
