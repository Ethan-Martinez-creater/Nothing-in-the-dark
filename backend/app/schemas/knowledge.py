from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateMemoryRequest(BaseModel):
    scope: Literal["working", "session", "case", "domain"] = "case"
    # platform_profile：跨案例的平台画像记忆（平台/用户特点，见
    # app/application/platform_profile.py），由采集与辩论链路维护。
    kind: Literal[
        "fact",
        "constraint",
        "preference",
        "correction",
        "summary",
        "platform_profile",
    ]
    content: str = Field(min_length=1, max_length=20_000)
    source_type: str = Field(min_length=1, max_length=64)
    # 模型可能把 URL/长 ID 当作 source_id 传入，100 太紧会导致
    # write_memory 校验失败（2026-08-08 冒烟发现），放宽到 500。
    source_id: str = Field(min_length=1, max_length=500)
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=1, ge=0, le=1)
    supersedes_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryResponse(BaseModel):
    id: str
    case_id: str | None
    scope: str
    kind: str
    content: str
    source_type: str
    source_id: str
    importance: float
    confidence: float
    active: bool
    supersedes_id: str | None
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    # M23: 治理字段（来源/信任/审核/有效期/敏感/版本/状态）。
    memory_type: str | None = None
    trust_level: str | None = None
    review_state: str | None = None
    confidence_level: str | None = None
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    last_verified_at: datetime | None = None
    content_hash: str | None = None
    version: int | None = None
    sensitivity: str | None = None
    index_status: str | None = None
    embedding_version: str | None = None
    write_policy_version: str | None = None
    status: str | None = None

    model_config = {"from_attributes": True}


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4_000)
    limit: int = Field(default=12, ge=1, le=50)
    platforms: list[str] | None = None
    time_range: dict[str, str | None] | None = None


class ExtractMemoryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    source_type: str = Field(default="conversation", min_length=1, max_length=64)
    source_id: str | None = None
    dedup_threshold: float = Field(default=0.85, ge=0, le=1)


class DecayMemoryRequest(BaseModel):
    ttl_days: int = Field(default=180, ge=1, le=3650)
    min_importance: float = Field(default=0.4, ge=0, le=1)


class DecayResultResponse(BaseModel):
    deactivated: int


class RagHitResponse(BaseModel):
    evidence_id: str
    source_type: str
    source_id: str
    content: str
    score: float
    retrieval_modes: list[str]
    platform: str | None = None
    source_url: str | None = None
    published_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class DocumentResponse(BaseModel):
    id: str
    case_id: str
    filename: str
    media_type: str
    checksum: str
    status: str
    metadata_json: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}
