"""Uncertainty & bias API contracts (08)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QualityAssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    target_type: str
    target_id: str
    dimension: str
    level: str
    score: float | None
    method: str
    inputs: dict[str, Any]
    limitations: list[str]
    version: str
    created_at: datetime


class ConclusionConfidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    conclusion_id: str
    conclusion_text: str
    dimensions: dict[str, Any]
    final_level: str
    forbidden_reasons: list[str]
    calibration_version: str
    created_at: datetime


class QualitySummaryResponse(BaseModel):
    case_id: str
    assessments: list[QualityAssessmentResponse]
    conclusions: list[ConclusionConfidenceResponse]


class CombineConfidenceRequest(BaseModel):
    dimensions: dict[str, str]


class CombineConfidenceResponse(BaseModel):
    final_level: str
    forbidden_reasons: list[str]


class HypothesisCreateRequest(BaseModel):
    statement: str = Field(min_length=1)
    prediction: str = ""
    supporting_evidence: list[str] = Field(default_factory=list)
    opposing_evidence: list[str] = Field(default_factory=list)
    proposer: str = "system"


class HypothesisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    statement: str
    prediction: str
    supporting_evidence: list[str]
    opposing_evidence: list[str]
    status: str
    proposer: str
    review_notes: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SensitivityRunRequest(BaseModel):
    baseline_params: dict[str, Any] = Field(default_factory=dict)
    variant_params: dict[str, Any] = Field(default_factory=dict)


class SensitivityRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    baseline_hash: str
    baseline_params: dict[str, Any]
    variant_params: dict[str, Any]
    output_diff: dict[str, Any]
    status: str
    cost: float
    created_at: datetime
    updated_at: datetime
