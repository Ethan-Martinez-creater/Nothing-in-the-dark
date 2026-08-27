from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class CreateCaseRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=500)
    title: str | None = Field(default=None, max_length=200)
    description: str = Field(default="", max_length=3000)
    platforms: list[str] = Field(default_factory=lambda: ["weibo", "bilibili"])
    time_start: str | None = None
    time_end: str | None = None
    project_id: str | None = None

    @field_validator("platforms")
    @classmethod
    def validate_platforms(cls, value: list[str]) -> list[str]:
        supported = {"weibo", "bilibili", "tieba", "zhihu", "douyin"}
        normalized = list(dict.fromkeys(item.strip().lower() for item in value))
        if not normalized:
            raise ValueError("At least one platform is required")
        unsupported = set(normalized) - supported
        if unsupported:
            raise ValueError(f"Unsupported platforms: {', '.join(sorted(unsupported))}")
        return normalized


class CaseResponse(BaseModel):
    id: str
    title: str
    topic: str
    description: str
    status: str
    platforms: list[str]
    time_range: dict[str, str | None]
    project_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CreateTurnRequest(BaseModel):
    content: str = Field(min_length=1, max_length=5000)


class RenameCaseRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class TurnResponse(BaseModel):
    id: str
    case_id: str
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}
