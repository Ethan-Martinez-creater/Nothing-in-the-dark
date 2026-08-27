"""Propagation edge API contracts (human confirmation)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConfirmPropagationEdgeRequest(BaseModel):
    confirmed: bool = Field(description="人工确认结论：observed 边是否成立")
    note: str | None = Field(
        default=None, max_length=500, description="人工复核备注"
    )


class PropagationEdgeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    source_post_id: str
    target_post_id: str
    relation: str
    confidence: float
    feature_scores: dict[str, Any]
    evidence_ids: list[str]
    algorithm_version: str
    human_confirmed: bool
