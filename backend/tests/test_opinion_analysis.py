"""M7b opinion intelligence: cosine K-Means clustering, themes, time
series, trend breakpoints and influencer ranking (pure stdlib)."""

from __future__ import annotations

from app.services.analysis import analyze_opinion
from app.services.opinion_analysis import (
    hourly_series,
    influencer_ranking,
    kmeans,
    opinion_groups,
    theme_for,
    trend_breakpoints,
)


def _posts(texts: list[str], published: list[str] | None = None) -> list[dict]:
    published = published or [
        "2026-08-01T00:00:00+00:00" for _ in texts
    ]
    return [
        {
            "id": f"post-{index}",
            "platform": "weibo",
            "author": f"author-{index % 3}",
            "content": text,
            "published_at": published[index],
            "like_count": index * 10,
            "comment_count": index,
            "share_count": index * 2,
            "follower_count": index * 100,
        }
        for index, text in enumerate(texts)
    ]


# ---------- clustering ----------


def test_kmeans_separates_distinct_themes() -> None:
    food = [f"食品安全问题曝光 添加剂超标 {i}" for i in range(6)]
    concert = [f"明星演唱会门票开售 粉丝抢购 {i}" for i in range(6)]
    assignments = kmeans(food + concert, k=2)
    food_ids = set(range(6))
    food_cluster = {assignments[i] for i in food_ids}
    concert_cluster = {assignments[i] for i in range(6, 12)}
    assert len(food_cluster) == 1
    assert len(concert_cluster) == 1
    assert food_cluster != concert_cluster


def test_kmeans_handles_tiny_and_empty_inputs() -> None:
    assert kmeans([]) == []
    assert kmeans(["单条内容"]) == [0]
    assert kmeans(["a" * 20, "b" * 20, "c" * 20], k=1) == [0, 0, 0]


def test_kmeans_deterministic() -> None:
    texts = [f"话题内容样本 {i} 讨论" for i in range(12)]
    assert kmeans(texts, k=3) == kmeans(texts, k=3)


# ---------- themes ----------


def test_theme_for_returns_distinctive_tokens() -> None:
    themes = theme_for(["食品安全 添加剂 曝光", "添加剂 超标 曝光", "食品 安全 投诉"])
    assert themes
    assert any("食品" in theme or "添加剂" in theme for theme in themes)
    assert theme_for([]) == []


# ---------- opinion groups ----------


def test_opinion_groups_share_sums_to_100() -> None:
    posts = _posts(
        [
            "食品安全问题曝光 添加剂超标",
            "食品添加剂问题 严重超标",
            "食品安全 投诉 曝光",
            "明星演唱会门票开售",
            "演唱会门票 粉丝抢购",
            "明星演唱会 热度很高",
        ]
    )
    texts = [post["content"] for post in posts]
    groups = opinion_groups(posts, texts)
    assert groups
    assert round(sum(group["share"] for group in groups)) == 100
    assert groups[0]["size"] >= groups[-1]["size"]  # largest first
    assert all(group["themes"] for group in groups)
    assert all(group["representative_post_id"] for group in groups)


# ---------- time series ----------


def test_hourly_series_buckets_by_hour_with_sentiment_share() -> None:
    posts = _posts(
        ["满意 很好", "非常满意", "差评 失望", "一般般"],
        published=[
            "2026-08-01T00:10:00+00:00",
            "2026-08-01T00:40:00+00:00",
            "2026-08-01T02:00:00+00:00",
            "2026-08-02T10:00:00+00:00",
        ],
    )
    sentiments = ["positive", "positive", "negative", "neutral"]
    series = hourly_series(posts, sentiments)
    assert [item["bucket"] for item in series] == [
        "2026-08-01T00:00",
        "2026-08-01T02:00",
        "2026-08-02T10:00",
    ]
    assert series[0]["count"] == 2
    assert series[0]["positive"] == 100.0
    assert series[1]["negative"] == 100.0


def test_trend_breakpoints_detect_surge() -> None:
    series = [
        {"bucket": f"h{i:02d}", "count": 2} for i in range(10)
    ]  # flat 2/hour
    series[5] = {"bucket": "h05", "count": 50}  # burst
    assert not trend_breakpoints(series[:6])  # too short
    breakpoints = trend_breakpoints(series)
    assert breakpoints
    assert breakpoints[0]["surge_ratio"] >= 2.0
    assert trend_breakpoints([]) == []


# ---------- influencers ----------


def test_influencer_ranking_orders_by_engagement() -> None:
    posts = [
        {
            "id": "p1",
            "author": "big",
            "content": "x",
            "like_count": 100,
            "comment_count": 10,
            "share_count": 5,
            "follower_count": 100000,
        },
        {
            "id": "p2",
            "author": "small",
            "content": "y",
            "like_count": 1,
            "comment_count": 0,
            "share_count": 0,
            "follower_count": 10,
        },
    ]
    ranked = influencer_ranking(posts)
    assert [item["author"] for item in ranked] == ["big", "small"]
    assert ranked[0]["engagement"] == 115
    assert influencer_ranking([]) == []


# ---------- integration through analyze_opinion ----------


async def test_analyze_opinion_appends_m7b_keys_without_narrative() -> None:
    posts = _posts(
        [
            "食品安全问题曝光 添加剂超标 严重",
            "食品添加剂 问题 严重超标",
            "明星演唱会门票开售 粉丝抢购",
        ],
        published=[
            "2026-08-01T00:00:00+00:00",
            "2026-08-01T01:00:00+00:00",
            "2026-08-02T12:00:00+00:00",
        ],
    )
    opinion = analyze_opinion(posts)
    assert "clusters" in opinion
    assert "time_series" in opinion
    assert "trends" in opinion
    assert "influencers" in opinion
    assert len(opinion["clusters"]) >= 1
    assert opinion["influencers"][0]["author"]
    # M7b must never add narrative keys or fixed text.
    assert "summary" not in opinion
    assert "key_findings" not in opinion


def test_analyze_opinion_empty_posts_stays_lean() -> None:
    opinion = analyze_opinion([])
    assert "clusters" not in opinion
    assert opinion["statistics"]["total_posts"] == 0
