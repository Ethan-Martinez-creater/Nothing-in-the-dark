"""P0-1.1a: grounded explanation of opinion statistics.

The explanation must be derived from computed keys (clusters / time_series /
trends / influencers / distributions) and cite real post IDs. It must not
invent numbers that are absent from the input statistics.
"""

from __future__ import annotations

from app.services.analysis import analyze_opinion
from app.services.opinion_analysis import explain_opinion_statistics


def _posts() -> list[dict[str, object]]:
    return [
        {
            "id": "post-weibo-1",
            "platform": "weibo",
            "author": "观察员甲",
            "content": "官方通报称事故正在调查，请等待结论",
            "published_at": "2026-08-01T08:00:00+00:00",
            "engagement": 80,
            "follower_count": 1200,
        },
        {
            "id": "post-bili-1",
            "platform": "bilibili",
            "author": "记录与核查",
            "content": "视频梳理事故时间线，多个版本说法不一致",
            "published_at": "2026-08-01T10:00:00+00:00",
            "engagement": 240,
            "follower_count": 8000,
        },
        {
            "id": "post-zhihu-1",
            "platform": "zhihu",
            "author": "理性讨论者",
            "content": "把目前可靠信息源梳理后，情绪化表述应去掉",
            "published_at": "2026-08-01T12:00:00+00:00",
            "engagement": 40,
            "follower_count": 400,
        },
    ]


def test_explain_opinion_statistics_cites_real_post_ids() -> None:
    posts = _posts()
    result = analyze_opinion(posts)
    explanation = explain_opinion_statistics(result, posts)

    assert explanation["source"] == "statistics"
    assert explanation["text"]
    assert "3" in explanation["text"]
    assert explanation["evidence_ids"]
    for evidence_id in explanation["evidence_ids"]:
        assert evidence_id in {"post-weibo-1", "post-bili-1", "post-zhihu-1"}


def test_explain_does_not_invent_missing_trend_numbers() -> None:
    result = {
        "statistics": {
            "total_posts": 2,
            "sentiment_distribution": {"positive": 0, "neutral": 50, "negative": 50},
            "platform_distribution": {"weibo": 2},
        },
        "clusters": [],
        "trends": [],
        "influencers": [],
    }
    explanation = explain_opinion_statistics(result, [])
    assert "突变" not in explanation["text"]
    assert explanation["evidence_ids"] == []


def test_analyze_opinion_embeds_explanation() -> None:
    result = analyze_opinion(_posts())
    assert "explanation" in result
    assert result["explanation"]["text"]
    assert result["explanation"]["evidence_ids"]
