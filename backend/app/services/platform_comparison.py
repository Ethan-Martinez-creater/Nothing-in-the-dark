"""Cross-platform alignment: how the same event unfolded across platforms.

把同一案例在多个平台的采集数据聚合为可直接可视化的对比结构：
参与度、情感分布、时间线、高频话题词、跨平台共现术语与规则化洞察。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

_STOP_TERMS = frozenset(
    {"一个", "这个", "那个", "什么", "怎么", "可以", "已经", "目前", "现在",
     "我们", "你们", "他们", "没有", "不是", "就是", "还是", "通过", "关于",
     "进行", "相关", "事件", "消息", "情况", "内容", "评论", "用户", "平台",
     "帖子", "视频", "发布", "讨论", "关注", "回应", "表示", "认为", "看到"}
)

_TIMELINE_BUCKET_MINUTES = 60


def _clean_bigrams(text: str) -> list[str]:
    """字符级 2-gram，过滤标点/空白与停用词（中文无空格分词，bigram 足够）。"""
    chars = [ch for ch in re.sub(r"[\s\W_]+", "", text)]
    grams = [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    return [g for g in grams if g not in _STOP_TERMS and not g.isdigit()]


def _bucket_key(published_at: str | None) -> str | None:
    if not published_at:
        return None
    try:
        dt = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M")


def _sentiment_distribution(posts: list[dict[str, Any]]) -> dict[str, float]:
    counts = Counter(str(post.get("sentiment") or "neutral") for post in posts)
    total = max(1, len(posts))
    return {
        label: round(counts.get(label, 0) * 100 / total, 1)
        for label in ("positive", "neutral", "negative")
    }


def _top_terms(posts: list[dict[str, Any]], limit: int = 5) -> list[str]:
    counter: Counter[str] = Counter()
    for post in posts:
        counter.update(_clean_bigrams(str(post.get("content") or "")))
    return [term for term, _ in counter.most_common(limit)]


def build_platform_comparison(
    posts: list[dict[str, Any]],
    *,
    topic: str = "",
) -> dict[str, Any]:
    """Aggregate posts by platform into a comparison structure.

    ``posts`` items need: platform, content, sentiment, engagement,
    published_at, author (nullable).
    """
    by_platform: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for post in posts:
        platform = str(post.get("platform") or "unknown")
        by_platform[platform].append(post)

    platforms = sorted(by_platform)
    participation: list[dict[str, Any]] = []
    sentiment: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    topic_terms: list[dict[str, Any]] = []
    first_posts: dict[str, str | None] = {}

    for platform in platforms:
        platform_posts = by_platform[platform]
        total_engagement = 0
        for post in platform_posts:
            engagement = post.get("engagement")
            if isinstance(engagement, dict):
                # source_posts.engagement 为 JSON：取 total 或求和数值字段
                engagement = engagement.get("total") or sum(
                    v for v in engagement.values() if isinstance(v, (int, float))
                )
            total_engagement += int(engagement or 0)
        participation.append(
            {
                "platform": platform,
                "posts": len(platform_posts),
                "total_engagement": total_engagement,
                "avg_engagement": round(
                    total_engagement / max(1, len(platform_posts)), 1
                ),
            }
        )
        sentiment.append(
            {
                "platform": platform,
                "distribution": _sentiment_distribution(platform_posts),
            }
        )
        topic_terms.append(
            {"platform": platform, "terms": _top_terms(platform_posts)}
        )
        # 时间线：按小时分桶
        buckets: Counter[str] = Counter()
        for post in platform_posts:
            key = _bucket_key(str(post.get("published_at") or ""))
            if key:
                buckets[key] += 1
        for window in sorted(buckets):
            timeline.append(
                {"platform": platform, "window": window, "posts": buckets[window]}
            )
        # 首发时间
        timestamps = [
            str(post.get("published_at") or "")
            for post in platform_posts
            if post.get("published_at")
        ]
        first_posts[platform] = min(timestamps) if timestamps else None

    # 跨平台共现术语：在 >=2 个平台出现的 top terms
    term_platforms: dict[str, set[str]] = defaultdict(set)
    for entry in topic_terms:
        for term in entry["terms"]:
            term_platforms[term].add(entry["platform"])
    common_terms = [
        {
            "term": term,
            "platforms": sorted(platforms),
        }
        for term, platforms_seen in term_platforms.items()
        if len(platforms_seen) >= 2
    ]

    # 规则化洞察
    insights: list[str] = []
    if first_posts:
        earliest = min(
            (p for p in first_posts.values() if p), default=None
        )
        if earliest:
            origin = next(
                (p for p, ts in first_posts.items() if ts == earliest), None
            )
            insights.append(
                f"{origin} 最早出现相关讨论（{earliest[:16]}），"
                f"其他平台随后跟进" if origin else ""
            )
    if participation:
        top = max(participation, key=lambda item: item["total_engagement"])
        insights.append(
            f"{top['platform']} 互动量最高（合计 {top['total_engagement']}），"
            f"是讨论最活跃的平台"
        )
    negative_leads = [
        entry
        for entry in sentiment
        if (entry["distribution"].get("negative") or 0) >= 40
    ]
    if negative_leads:
        names = "、".join(entry["platform"] for entry in negative_leads)
        insights.append(f"{names} 负面情绪占比超过 40%，情绪更激烈")
    if common_terms:
        insights.append(
            f"检测到 {len(common_terms)} 组跨平台共现话题词，"
            f"同一事件的多平台传播特征明显"
        )
    if topic and not insights:
        insights.append("当前样本内未发现显著跨平台差异信号")

    return {
        "platforms": platforms,
        "participation": participation,
        "sentiment": sentiment,
        "timeline": timeline,
        "topic_terms": topic_terms,
        "common_terms": common_terms,
        "insights": insights,
    }
