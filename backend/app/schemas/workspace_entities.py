"""V3 §33: Workspace Entity DTOs（entity_type 第一版固定 account）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkspaceEntityListItem(BaseModel):
    entity_id: str
    entity_type: str
    canonical_name: str
    platforms: list[str] = Field(default_factory=list)
    investigation_count: int = 0
    post_count: int = 0
    comment_count: int = 0
    last_seen_at: datetime | None = None
    risk_summary: str | None = None


class WorkspaceEntityListResponse(BaseModel):
    items: list[WorkspaceEntityListItem]
    total: int


class WorkspaceEntityProfileResponse(BaseModel):
    entity_id: str
    component_key: str
    entity_ids: list[str]
    entity_type: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    platform_identities: list[dict[str, str]] = Field(default_factory=list)
    investigation_count: int = 0
    investigations: list[str] = Field(default_factory=list)
    post_count: int = 0
    comment_count: int = 0
    engagement_total: int = 0
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    recent_posts: list[dict[str, Any]] = Field(default_factory=list)
    risk_assessments: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_local_risk: list[dict[str, Any]] = Field(default_factory=list)
    coordination_memberships: list[dict[str, Any]] = Field(default_factory=list)
    algorithm_version: str
