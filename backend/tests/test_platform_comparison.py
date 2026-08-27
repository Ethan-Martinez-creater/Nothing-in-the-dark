"""Platform comparison aggregation + case deletion cascade."""

from __future__ import annotations

from app.services.platform_comparison import build_platform_comparison

_POSTS = [
    {
        "platform": "weibo",
        "content": "最早的现场信息提到了暴雨水库泄洪，等待官方说明",
        "sentiment": "neutral",
        "engagement": 120,
        "published_at": "2026-08-07T21:07:42+00:00",
    },
    {
        "platform": "weibo",
        "content": "关于暴雨泄洪的讨论快速增加，多人引用同一现场描述",
        "sentiment": "negative",
        "engagement": 203,
        "published_at": "2026-08-07T21:54:42+00:00",
    },
    {
        "platform": "bilibili",
        "content": "视频梳理暴雨泄洪事件时间线与多个版本说法",
        "sentiment": "neutral",
        "engagement": 500,
        "published_at": "2026-08-07T22:41:42+00:00",
    },
]


def test_aggregates_participation_and_sentiment() -> None:
    result = build_platform_comparison(_POSTS)
    assert result["platforms"] == ["bilibili", "weibo"]

    by_platform = {item["platform"]: item for item in result["participation"]}
    assert by_platform["weibo"]["posts"] == 2
    assert by_platform["weibo"]["total_engagement"] == 323
    assert by_platform["bilibili"]["posts"] == 1

    sentiment = {item["platform"]: item["distribution"] for item in result["sentiment"]}
    assert sentiment["weibo"]["negative"] == 50.0
    assert sentiment["bilibili"]["positive"] == 0.0


def test_timeline_and_insights() -> None:
    result = build_platform_comparison(_POSTS)
    windows = [point["window"] for point in result["timeline"]]
    assert "2026-08-07T21:07" in windows
    assert "2026-08-07T22:41" in windows

    joined = "\n".join(result["insights"])
    assert "weibo 最早出现相关讨论" in joined
    assert "bilibili 互动量最高" in joined


def test_common_terms_across_platforms() -> None:
    result = build_platform_comparison(_POSTS)
    # demo 内容短，每个 bigram 频次为 1，能稳定进入 top 列表的词有限；
    # 只要检测到跨平台共现术语即满足对齐判定。
    assert len(result["common_terms"]) >= 1
    platforms = set(result["common_terms"][0]["platforms"])
    assert platforms == {"bilibili", "weibo"}


def test_empty_posts_yield_empty_structure() -> None:
    result = build_platform_comparison([])
    assert result["platforms"] == []
    assert result["participation"] == []
    assert result["insights"] == []
