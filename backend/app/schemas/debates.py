"""Debate API contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CreateDebateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class UserMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=5000)


class DebateResponse(BaseModel):
    id: str
    case_id: str
    title: str
    status: str
    round: int
    platform_roles: dict[str, object]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DebateMessageResponse(BaseModel):
    id: str
    debate_id: str
    role: str
    platform: str | None
    round: int
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DebateVoteResponse(BaseModel):
    id: str
    debate_id: str
    platform: str
    choice: str
    reason: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DebateDetailResponse(DebateResponse):
    messages: list[DebateMessageResponse]
    votes: list[DebateVoteResponse]
