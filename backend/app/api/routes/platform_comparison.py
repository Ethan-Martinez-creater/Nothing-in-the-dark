"""Cross-platform comparison endpoint (跨平台数据对齐)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.services.platform_comparison import build_platform_comparison

router = APIRouter()


@router.get("/{case_id}/platform-comparison")
async def get_platform_comparison(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict:
    """Aggregate the case's collected posts across platforms into a
    comparison structure (participation / sentiment / timeline / terms /
    common terms / insights) for visualization."""
    repository = container.repository
    await repository.get_case(case_id)  # 404 for unknown case
    posts = await container.social.list_posts_by_case(case_id)
    return build_platform_comparison(
        [
            {
                "platform": post.platform,
                "content": post.content,
                # SourcePostRecord 无 sentiment 列：情感存于 raw_payload。
                "sentiment": (post.raw_payload or {}).get("sentiment") or "neutral",
                "engagement": post.engagement,
                "published_at": post.published_at.isoformat()
                if post.published_at
                else None,
                "author": post.author_name or post.author_id,
            }
            for post in posts
        ],
        topic="",
    )
