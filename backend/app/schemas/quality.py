"""V3 §23: Investigation Quality response DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class QualityDimensionResponse(BaseModel):
    key: str
    label: str
    weight: int
    score: float | None = None
    available: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)


class QualityGapResponse(BaseModel):
    code: str
    severity: str
    object_type: str
    object_id: str | None = None
    message: str
    action: dict[str, Any] = Field(default_factory=dict)


class QualityWarningResponse(BaseModel):
    code: str
    message: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class InvestigationQualityResponse(BaseModel):
    case_id: str
    overall_score: float | None = None
    grade: str
    dimensions: list[QualityDimensionResponse]
    gaps: list[QualityGapResponse]
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer: str
    computed_at: datetime
    algorithm_version: str
    input_fingerprint: str


class HomeQualityAttentionItem(BaseModel):
    case_id: str
    overall_score: float | None = None
    grade: str
    computed_at: datetime
