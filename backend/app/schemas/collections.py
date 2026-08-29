"""M3: Collection Definition API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.infrastructure.database.models import CollectionDefinitionRecord


class CreateCollectionDefinitionRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    platforms: list[str] = Field(min_length=1)
    platform_queries: dict[str, list[str]] = Field(default_factory=dict)
    exclusions: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)


class GenerateCollectionDefinitionRequest(BaseModel):
    goal: str | None = Field(default=None, max_length=2000)


class ReviseCollectionDefinitionRequest(BaseModel):
    goal: str | None = Field(default=None, max_length=2000)
    platforms: list[str] | None = None
    platform_queries: dict[str, list[str]] | None = None
    exclusions: list[str] | None = None
    filters: dict[str, Any] | None = None


class CollectionDefinitionResponse(BaseModel):
    id: str
    case_id: str
    version: int
    status: str
    goal: str
    platforms: list[str]
    platform_queries: dict[str, Any]
    exclusions: list[str]
    filters: dict[str, Any]
    generated_by_run_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_record(cls, record: CollectionDefinitionRecord) -> CollectionDefinitionResponse:
        return cls(
            id=record.id,
            case_id=record.case_id,
            version=record.version,
            status=record.status,
            goal=record.goal,
            platforms=list(record.platforms or []),
            platform_queries=dict(record.platform_queries or {}),
            exclusions=list(record.exclusions or []),
            filters=dict(record.filters or {}),
            generated_by_run_id=record.generated_by_run_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
