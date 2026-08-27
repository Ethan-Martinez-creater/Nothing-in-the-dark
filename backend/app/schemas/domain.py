"""Read models for accounts, evaluations and cost summaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str | None
    platform: str
    native_id: str
    name: str
    normalized_name: str
    follower_count: int
    is_authoritative: bool


class EvaluationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str | None
    run_id: str | None
    metric: str
    score: float
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CostSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    summary_type: str
    run_id: str | None
    case_id: str | None
    model_cost: float
    tool_cost: float
    total_cost: float
    currency: str
