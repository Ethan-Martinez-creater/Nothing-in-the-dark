"""Media pipeline API contracts (04)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class MediaAssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    post_id: str | None
    platform: str
    media_type: str
    url: str
    normalized_url: str
    source_kind: str
    storage_uri: str | None
    byte_size: int
    mime_type: str
    duration_ms: int | None
    width: int | None
    height: int | None
    download_status: str
    analysis_status: str
    error_code: str | None
    actual_sha256: str | None
    hash_kind: str
    phash: str | None
    ocr_text: str | None
    keyframe_urls: list[str]
    c2pa_status: str | None
    pipeline_version: str
    metadata_json: dict[str, Any]
    created_at: datetime


class MediaTranscriptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    asset_id: str
    kind: str
    language: str
    segments: list[dict[str, Any]]
    full_text: str
    confidence: float
    provider: str
    version: str
    created_at: datetime


class MediaDerivativeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    asset_id: str
    kind: str
    storage_uri: str | None
    sha256: str | None
    time_start_ms: int | None
    time_end_ms: int | None
    bbox: dict[str, Any] | None
    metadata_json: dict[str, Any]
    producer: str
    version: str
    created_at: datetime


class MediaAssetDetailResponse(MediaAssetResponse):
    transcripts: list[MediaTranscriptResponse] = []
    derivatives: list[MediaDerivativeResponse] = []


class BackfillRequest(BaseModel):
    limit: int = 50


class BackfillResponse(BaseModel):
    enqueued: int
