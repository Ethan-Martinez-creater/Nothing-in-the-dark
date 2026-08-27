"""DemoCrawlerAdapter：全平台覆盖 / 时间范围散布 / 数量上限。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.ports.crawler import CrawlRequest
from app.infrastructure.crawler.demo import DemoCrawlerAdapter


def _collect(request: CrawlRequest) -> list[dict[str, object]]:
    import asyncio

    return asyncio.run(DemoCrawlerAdapter().collect(request))


def test_covers_all_selected_platforms() -> None:
    posts = _collect(
        CrawlRequest(
            topic="竹知了",
            platforms=["weibo", "bilibili", "zhihu", "douyin", "tieba"],
            time_range={},
        )
    )
    platforms = {str(post["platform"]) for post in posts}
    assert platforms == {"weibo", "bilibili", "zhihu", "douyin", "tieba"}
    assert all(post["is_demo"] is True for post in posts)
    # 每平台多条模板（不再是微博/B站各 3 条的旧形态）。
    for platform in platforms:
        assert sum(1 for post in posts if post["platform"] == platform) >= 6


def test_spreads_publish_times_within_requested_range() -> None:
    start = datetime(2026, 7, 16, tzinfo=UTC)
    end = datetime(2026, 8, 16, tzinfo=UTC)
    posts = _collect(
        CrawlRequest(
            topic="竹知了",
            platforms=["weibo"],
            time_range={"start": start.isoformat(), "end": end.isoformat()},
        )
    )
    assert posts
    stamps = [datetime.fromisoformat(str(post["published_at"])) for post in posts]
    assert all(start <= stamp <= end + timedelta(days=1) for stamp in stamps)
    # 散布而非挤在同一时刻：首条贴起点，末条接近终点。
    assert stamps[0] - start < timedelta(days=2)
    assert stamps[-1] > start + (end - start) / 2


def test_short_window_never_generates_posts_outside_bounds() -> None:
    start = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
    end = start + timedelta(minutes=2)
    posts = _collect(
        CrawlRequest(
            topic="午夜短窗口",
            platforms=["weibo"],
            time_range={"start": start.isoformat(), "end": end.isoformat()},
        )
    )
    assert posts
    stamps = [datetime.fromisoformat(str(post["published_at"])) for post in posts]
    assert all(start <= stamp <= end for stamp in stamps)


def test_respects_limit_per_platform() -> None:
    posts = _collect(
        CrawlRequest(
            topic="竹知了",
            platforms=["weibo", "zhihu"],
            time_range={},
            limit_per_platform=3,
        )
    )
    assert len(posts) == 6
    assert sum(1 for post in posts if post["platform"] == "weibo") == 3


def test_topic_is_interpolated_into_content() -> None:
    posts = _collect(
        CrawlRequest(topic="竹知了", platforms=["weibo"], time_range={})
    )
    assert all("竹知了" in str(post["content"]) for post in posts)
    assert all("{topic}" not in str(post["content"]) for post in posts)
