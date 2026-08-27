"""Project API contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class RenameProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ProjectResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
