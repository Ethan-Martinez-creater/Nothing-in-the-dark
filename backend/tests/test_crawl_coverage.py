"""Time-continuous crawl sampling: per-day cap, ranking, comment cap, stats."""

from __future__ import annotations

from app.application.ports.crawler import CrawlRequest
from app.services.crawl_coverage import (
    apply_coverage,
    detect_special_terms,
    format_coverage_memory,
    has_media,
    select_ranked,
)


def _post(
    post_id: str,
    *,
    day: str,
    content: str,
    engagement: int = 10,
    comments: list[dict] | None = None,
    platform: str = "weibo",
    media: bool = False,
) -> dict:
    item = {
        "id": post_id,
        "native_id": post_id,
        "platform": platform,
        "content": content,
        "published_at": f"{day}T10:00:00+00:00",
        "engagement": engagement,
        "metrics": {"liked_count": engagement},
        "comments": comments or [],
    }
    if media:
        item["content_type"] = "video"
        item["url"] = "https://example.invalid/v.mp4"
    return item


def test_drops_short_text_unless_media() -> None:
    kept, stats = select_ranked(
        [
            _post("s", day="2026-08-01", content="嗯", engagement=99),
            _post("m", day="2026-08-01", content="短", engagement=1, media=True),
            _post("ok", day="2026-08-01", content="这是一条足够长的现场描述。", engagement=5),
        ],
        limit=10,
    )
    ids = {item["id"] for item in kept}
    assert "s" not in ids
    assert "m" in ids
    assert "ok" in ids
    assert stats["dropped_short"] == 1
    assert has_media(kept[0]) or kept[0]["id"] == "ok"


def test_near_duplicate_counted_and_excluded() -> None:
    kept, stats = select_ranked(
        [
            _post("a", day="2026-08-01", content="官方通报已经发布请勿传谣", engagement=8),
            _post("b", day="2026-08-01", content="官方通报已经发布，请勿传谣！", engagement=80),
            _post("c", day="2026-08-01", content="完全不同的一条现场目击者记录。", engagement=3),
        ],
        limit=10,
    )
    assert stats["dropped_duplicate"] == 1
    assert {item["id"] for item in kept} == {"b", "c"}


def test_ranks_by_engagement_and_respects_limit() -> None:
    items = [
        _post(f"p{i}", day="2026-08-01", content=f"现场记录编号 {i} 详情补充", engagement=i)
        for i in range(5)
    ]
    kept, stats = select_ranked(items, limit=2)
    assert [item["id"] for item in kept] == ["p4", "p3"]
    assert stats["dropped_other"] == 3


def test_covers_each_day_independently() -> None:
    posts = []
    for day in ("2026-08-01", "2026-08-02", "2026-08-03"):
        for index in range(4):
            posts.append(
                _post(
                    f"{day}-{index}",
                    day=day,
                    content=f"{day} 的第 {index} 条连续观察记录",
                    engagement=index,
                )
            )
    result = apply_coverage(
        posts,
        CrawlRequest(
            topic="泄洪",
            platforms=["weibo"],
            time_range={
                "start": "2026-08-01",
                "end": "2026-08-03",
            },
            limit_per_platform=2,
            per_day_limit=2,
        ),
    )
    assert result.stats.empty_days == []
    by_day: dict[str, int] = {}
    for post in result.posts:
        by_day[post["published_at"][:10]] = by_day.get(post["published_at"][:10], 0) + 1
    assert by_day == {"2026-08-01": 2, "2026-08-02": 2, "2026-08-03": 2}
    assert all(bucket.raw_count == 4 and bucket.kept == 2 for bucket in result.stats.buckets)


def test_comment_cap_keeps_ranked_top() -> None:
    comments = [
        {"content": "文明", "engagement": 1, "metrics": {"like_count": 1}},
        {
            "content": "这条评论足够长而且点赞最高应该留下",
            "engagement": 90,
            "metrics": {"like_count": 90},
        },
        {
            "content": "另一条也比较长的现场补充说明文字",
            "engagement": 40,
            "metrics": {"like_count": 40},
        },
        {
            "content": "第三条较长评论内容用于占位排序",
            "engagement": 10,
            "metrics": {"like_count": 10},
        },
    ]
    result = apply_coverage(
        [
            _post(
                "p1",
                day="2026-08-01",
                content="事件现场的完整经过已经整理如下。",
                comments=comments,
            )
        ],
        CrawlRequest(
            topic="泄洪",
            platforms=["weibo"],
            time_range={
                "start": "2026-08-01T00:00:00+00:00",
                "end": "2026-08-01T23:00:00+00:00",
            },
            comment_limit=2,
        ),
    )
    kept = result.posts[0]["comments"]
    assert len(kept) == 2
    assert kept[0]["engagement"] == 90
    assert result.stats.comment_raw == 4
    assert result.stats.comment_kept == 2
    assert result.stats.comment_dropped_short >= 1


def test_special_terms_flag_comment_only_bursts() -> None:
    comments = [
        {"content": f"文明 {index}", "engagement": 1}
        for index in range(4)
    ]
    terms = detect_special_terms(
        [
            _post(
                "p1",
                day="2026-08-01",
                content="水库调度说明已经发布请看原文。",
                comments=comments,
            )
        ]
    )
    assert any(item["term"] == "文明" for item in terms)


def test_memory_text_includes_counts() -> None:
    result = apply_coverage(
        [
            _post("p1", day="2026-08-01", content="足够长度的第一条观察。", engagement=3),
            _post("p2", day="2026-08-01", content="足够长度的第一条观察。", engagement=1),
        ],
        CrawlRequest(
            topic="泄洪谣言",
            platforms=["weibo"],
            time_range={
                "start": "2026-08-01T00:00:00+00:00",
                "end": "2026-08-02T23:00:00+00:00",
            },
        ),
    )
    text = format_coverage_memory("泄洪谣言", result.stats)
    assert "泄洪谣言" in text
    assert "近重复" in text
    assert "2026-08-02" in text or "空窗" in text
    assert result.stats.time_filter_mode == "post_filter"
    assert result.stats.historical_completeness is False
    assert "不保证完整历史覆盖" in text
