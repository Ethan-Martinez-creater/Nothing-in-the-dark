"""C8.3: Raw posts routes（Live Data Posts 列表 + C8.2 时间聚合）。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.posts import (
    PostResponse,
    PostsPageResponse,
    PostsStatsResponse,
)

router = APIRouter()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@router.get("/{case_id}/posts", response_model=PostsPageResponse)
async def list_case_posts(
    case_id: str,
    platform: str | None = None,
    q: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    container: ApplicationContainer = Depends(get_container),
) -> PostsPageResponse:
    """分页原始帖子（platform/关键词/时间范围过滤，时间倒序）。"""
    await container.repository.get_case(case_id)
    records = await container.social.list_posts_page(
        case_id,
        platform=platform,
        q=q,
        date_from=_parse_dt(from_),
        date_to=_parse_dt(to),
        limit=limit + 1,
        offset=offset,
    )
    has_more = len(records) > limit
    return PostsPageResponse(
        posts=[PostResponse.from_record(record) for record in records[:limit]],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


@router.get("/{case_id}/posts:stats", response_model=PostsStatsResponse)
async def get_post_stats(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> PostsStatsResponse:
    """Volume/Platform Timeline 聚合：按天总量 + 按天×平台计数。"""
    await container.repository.get_case(case_id)
    rows = await container.social.list_post_time_rows(case_id)
    volume: dict[str, int] = {}
    platform_day: dict[tuple[str, str], int] = {}
    total = 0
    for published_at, platform in rows:
        total += 1
        day = (
            published_at.astimezone(timezone.utc).date().isoformat()
            if published_at
            else "unknown"
        )
        volume[day] = volume.get(day, 0) + 1
        key = (platform, day)
        platform_day[key] = platform_day.get(key, 0) + 1
    return PostsStatsResponse(
        total=total,
        volume_by_day=[
            {"day": day, "count": count}
            for day, count in sorted(volume.items())
        ],
        platform_by_day=[
            {"platform": platform, "day": day, "count": count}
            for (platform, day), count in sorted(platform_day.items())
        ],
    )
