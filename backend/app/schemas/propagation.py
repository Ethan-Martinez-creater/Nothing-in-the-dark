"""Propagation edge API contracts (human confirmation) + graph DTO (C7)."""

from __future__ import annotations

from typing import Any, Literal

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
    # FC1: 显式三态（unreviewed/confirmed/rejected）；human_confirmed 仅兼容。
    human_review_state: Literal["unreviewed", "confirmed", "rejected"]


class PropagationGraphNode(BaseModel):
    """C7: 图节点 DTO —— 按 post 去重的 node 视图。

    同一 post 的多个 candidate role（source/bridge/burst/hub）聚合为
    roles 列表；主 role 与 score 取最高分项。数据全部来自
    PropagationNodeRecord + SourcePostRecord 既有字段。
    """

    post_id: str
    role: str
    roles: list[str]
    score: float
    attributes: dict[str, Any]
    algorithm_version: str
    platform: str
    label: str
    excerpt: str
    published_at: str | None = None
    author_name: str = ""


class PropagationGraphResponse(BaseModel):
    nodes: list[PropagationGraphNode]
    edges: list[PropagationEdgeResponse]
