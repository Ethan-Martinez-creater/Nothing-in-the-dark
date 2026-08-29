"""C8.3: Raw posts 分页 API 契约 + C8.2 时间聚合。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.infrastructure.database.models import SourcePostRecord


class PostResponse(BaseModel):
    """仅暴露稳定字段；raw_payload/embedding/content_hash 不对外。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    platform: str
    native_id: str
    content_type: str
    title: str
    content: str
    author_name: str
    source_url: str
    published_at: datetime | None
    engagement: dict[str, Any]

    @classmethod
    def from_record(cls, record: SourcePostRecord) -> PostResponse:
        return cls(
            id=record.id,
            platform=record.platform,
            native_id=record.native_id,
            content_type=record.content_type,
            title=record.title or "",
            content=record.content or "",
            author_name=record.author_name or "",
            source_url=record.source_url or "",
            published_at=record.published_at,
            engagement=dict(record.engagement or {}),
        )


class PostsPageResponse(BaseModel):
    posts: list[PostResponse]
    limit: int
    offset: int
    has_more: bool


class PostsStatsResponse(BaseModel):
    """按天总量与按天×平台计数（Timeline Workspace 聚合源）。"""

    total: int
    volume_by_day: list[dict[str, Any]]
    platform_by_day: list[dict[str, Any]]
