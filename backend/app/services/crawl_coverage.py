"""Time-continuous, non-random crawl sampling.

采集结果按「天」分桶覆盖用户选择的时间区间：每天保留排序后的前
``per_day_limit`` 条（默认 150），评论每帖保留前 ``comment_limit`` 条
（默认 10）。短文本丢弃（除非带图/视频），近重复丢弃并计入统计。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.application.ports.crawler import CrawlRequest

PER_DAY_LIMIT = 150
COMMENT_LIMIT = 10
MIN_TEXT_LEN = 10
NEAR_DUP_THRESHOLD = 0.86
MAX_UPSTREAM_FETCH = 600
CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]{2,8}")
# 评论区相对主贴异常高频、且常被反讽/玩梗挪用的短词。
_MARKED_TERMS = (
    "文明",
    "素质",
    "理性",
    "客观",
    "爱国",
    "辟谣",
    "官方",
    "明白",
    "懂的",
    "好家伙",
    "细思极恐",
)


@dataclass(slots=True)
class BucketStats:
    day: str
    platform: str
    raw_count: int = 0
    kept: int = 0
    dropped_short: int = 0
    dropped_duplicate: int = 0
    duplicate_groups: int = 0
    similar_groups: int = 0
    dropped_other: int = 0


@dataclass(slots=True)
class CoverageStats:
    per_day_limit: int
    comment_limit: int
    days: list[str]
    buckets: list[BucketStats] = field(default_factory=list)
    comment_raw: int = 0
    comment_kept: int = 0
    comment_dropped_short: int = 0
    comment_dropped_duplicate: int = 0
    special_terms: list[dict[str, Any]] = field(default_factory=list)
    empty_days: list[str] = field(default_factory=list)
    day_evenness: float = 0.0
    time_filter_mode: str = "post_filter"
    historical_completeness: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "per_day_limit": self.per_day_limit,
            "comment_limit": self.comment_limit,
            "days": list(self.days),
            "empty_days": list(self.empty_days),
            "day_evenness": self.day_evenness,
            "time_filter_mode": self.time_filter_mode,
            "historical_completeness": self.historical_completeness,
            "comment_raw": self.comment_raw,
            "comment_kept": self.comment_kept,
            "comment_dropped_short": self.comment_dropped_short,
            "comment_dropped_duplicate": self.comment_dropped_duplicate,
            "special_terms": list(self.special_terms),
            "buckets": [
                {
                    "day": bucket.day,
                    "platform": bucket.platform,
                    "raw_count": bucket.raw_count,
                    "kept": bucket.kept,
                    "dropped_short": bucket.dropped_short,
                    "dropped_duplicate": bucket.dropped_duplicate,
                    "duplicate_groups": bucket.duplicate_groups,
                    "dropped_other": bucket.dropped_other,
                }
                for bucket in self.buckets
            ],
        }


@dataclass(slots=True)
class CoverageResult:
    posts: list[dict[str, Any]]
    stats: CoverageStats


def parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            if text.isdigit():
                number = int(text)
                if number > 10_000_000_000:
                    number //= 1000
                stamp = datetime.fromtimestamp(number, tz=UTC)
            else:
                stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=CHINA_TIMEZONE)
    return stamp.astimezone(UTC)


def _window_bound(value: object, *, end_of_day: bool) -> datetime | None:
    if isinstance(value, str) and len(value.strip()) == 10:
        local = datetime.fromisoformat(value.strip()).replace(tzinfo=CHINA_TIMEZONE)
        if end_of_day:
            local = local.replace(hour=23, minute=59, second=59, microsecond=999999)
        return local.astimezone(UTC)
    return parse_datetime(value)


def resolve_window(time_range: dict[str, str | None] | None) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    start = _window_bound((time_range or {}).get("start"), end_of_day=False)
    end = _window_bound((time_range or {}).get("end"), end_of_day=True)
    if start is None and end is None:
        local_now = now.astimezone(CHINA_TIMEZONE)
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return local_start.astimezone(UTC), now
    if end is None:
        end = now
    if start is None or start >= end:
        local_end = end.astimezone(CHINA_TIMEZONE)
        start = local_end.replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(UTC)
        if start >= end:
            start = end - timedelta(hours=1)
    return start, end


def iter_days(start: datetime, end: datetime) -> list[date]:
    first = start.astimezone(CHINA_TIMEZONE).date()
    last = end.astimezone(CHINA_TIMEZONE).date()
    days: list[date] = []
    cursor = first
    while cursor <= last:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def day_key(value: object) -> str:
    stamp = parse_datetime(value)
    if stamp is None:
        return "unknown"
    return stamp.astimezone(CHINA_TIMEZONE).date().isoformat()


def effective_caps(request: CrawlRequest) -> tuple[int, int]:
    per_day = max(int(getattr(request, "per_day_limit", PER_DAY_LIMIT) or PER_DAY_LIMIT), 1)
    comment = max(int(getattr(request, "comment_limit", COMMENT_LIMIT) or COMMENT_LIMIT), 1)
    platform_limit = max(int(request.limit_per_platform or per_day), 1)
    return min(per_day, platform_limit), comment


def fetch_limit_for(request: CrawlRequest) -> int:
    start, end = resolve_window(request.time_range)
    days = max(len(iter_days(start, end)), 1)
    per_day, _ = effective_caps(request)
    return min(max(per_day * days, per_day), MAX_UPSTREAM_FETCH)


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", text)
    return text.casefold()


def _char_ngrams(text: str, size: int = 3) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def jaccard(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    a = _char_ngrams(left)
    b = _char_ngrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def has_media(item: dict[str, Any]) -> bool:
    content_type = str(item.get("content_type") or "").lower()
    if any(token in content_type for token in ("video", "image", "photo", "gif")):
        return True
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    media_keys = (
        "image_list",
        "images",
        "video_url",
        "aweme_url",
        "cover_url",
        "note_download_url",
        "video_id",
        "image_urls",
        "keyframes",
    )
    for key in media_keys:
        value = item.get(key) if item.get(key) is not None else raw.get(key)
        if isinstance(value, list) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def item_text(item: dict[str, Any]) -> str:
    parts = [str(item.get("title") or ""), str(item.get("content") or "")]
    return "\n".join(part for part in parts if part).strip()


def _metric_sum(item: dict[str, Any]) -> int:
    metrics = item.get("metrics")
    if isinstance(metrics, dict):
        total = 0
        for value in metrics.values():
            try:
                total += int(value or 0)
            except (TypeError, ValueError):
                continue
        return total
    try:
        return int(item.get("engagement") or 0)
    except (TypeError, ValueError):
        return 0


def importance_score(item: dict[str, Any]) -> float:
    text = item_text(item)
    comments = item.get("comments")
    comment_bonus = len(comments) * 4 if isinstance(comments, list) else 0
    media_bonus = 25.0 if has_media(item) else 0.0
    length_bonus = min(len(text), 240) / 40.0
    return float(_metric_sum(item) + comment_bonus + media_bonus + length_bonus)


def _too_short(item: dict[str, Any]) -> bool:
    return len(item_text(item)) < MIN_TEXT_LEN and not has_media(item)


def select_ranked(
    items: list[dict[str, Any]],
    limit: int,
    topic: str = "",
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Filter short/near-dup items, rank by importance, keep ``limit``.
    高重复/相似度内容也要保留并统计（水军/机器人矩阵证据）。
    """
    dropped_short = 0
    long_enough: list[dict[str, Any]] = []
    for item in items:
        if _too_short(item):
            dropped_short += 1
            continue
        long_enough.append(item)
    ranked = sorted(long_enough, key=importance_score, reverse=True)
    kept: list[dict[str, Any]] = []
    kept_norms: list[str] = []
    dropped_duplicate = 0
    similar_groups: int = 0
    for item in ranked:
        if len(kept) >= max(limit, 0):
            break
        norm = normalize_text(item_text(item))
        jaccards = [jaccard(norm, existing) for existing in kept_norms]
        if any(j >= NEAR_DUP_THRESHOLD for j in jaccards):
            dropped_duplicate += 1
            similar_groups += sum(1 for j in jaccards if j >= NEAR_DUP_THRESHOLD)
            continue
        kept_norms.append(norm)
        kept.append(item)
    dropped_other = max(len(ranked) - len(kept) - dropped_duplicate, 0)
    return kept, {
        "raw_count": len(items),
        "kept": len(kept),
        "dropped_short": dropped_short,
        "dropped_duplicate": dropped_duplicate,
        "duplicate_groups": dropped_duplicate,
        "similar_groups": similar_groups,
        "dropped_other": dropped_other,
    }


def apply_comment_coverage(
    comments: list[dict[str, Any]],
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    return select_ranked(comments, limit)


def apply_coverage(
    posts: list[dict[str, Any]],
    request: CrawlRequest,
) -> CoverageResult:
    per_day, comment_limit = effective_caps(request)
    start, end = resolve_window(request.time_range)
    days = [day.isoformat() for day in iter_days(start, end)]
    stats = CoverageStats(
        per_day_limit=per_day,
        comment_limit=comment_limit,
        days=days,
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for post in posts:
        platform = str(post.get("platform") or "unknown")
        grouped[(day_key(post.get("published_at")), platform)].append(post)

    kept_posts: list[dict[str, Any]] = []
    platforms = {str(post.get("platform") or "unknown") for post in posts} or set(
        request.platforms
    )
    for day in [*days, "unknown"]:
        for platform in sorted(platforms):
            bucket_items = grouped.get((day, platform), [])
            if not bucket_items and day == "unknown":
                continue
            selected, counts = select_ranked(bucket_items, per_day, topic=request.topic)
            comment_raw = 0
            comment_kept = 0
            comment_short = 0
            comment_dup = 0
            refined: list[dict[str, Any]] = []
            for post in selected:
                raw_comments = post.get("comments")
                if not isinstance(raw_comments, list):
                    refined.append(post)
                    continue
                kept_comments, comment_counts = apply_comment_coverage(
                    [item for item in raw_comments if isinstance(item, dict)],
                    comment_limit,
                )
                next_post = dict(post)
                next_post["comments"] = kept_comments
                refined.append(next_post)
                comment_raw += comment_counts["raw_count"]
                comment_kept += comment_counts["kept"]
                comment_short += comment_counts["dropped_short"]
                comment_dup += comment_counts["dropped_duplicate"]
            stats.comment_raw += comment_raw
            stats.comment_kept += comment_kept
            stats.comment_dropped_short += comment_short
            stats.comment_dropped_duplicate += comment_dup
            if bucket_items or day in days:
                stats.buckets.append(
                    BucketStats(
                        day=day,
                        platform=platform,
                        raw_count=counts["raw_count"],
                        kept=counts["kept"],
                        dropped_short=counts["dropped_short"],
                        dropped_duplicate=counts["dropped_duplicate"],
                        duplicate_groups=counts["duplicate_groups"],
                        similar_groups=counts.get("similar_groups", 0),
                        dropped_other=counts["dropped_other"],
                    )
                )
            kept_posts.extend(refined)

    filled_days = {bucket.day for bucket in stats.buckets if bucket.kept > 0}
    stats.empty_days = [day for day in days if day not in filled_days]
    per_day_kept = [
        sum(bucket.kept for bucket in stats.buckets if bucket.day == day)
        for day in days
    ]
    stats.day_evenness = shannon_evenness(per_day_kept)
    stats.special_terms = detect_special_terms(kept_posts)
    kept_posts.sort(
        key=lambda post: parse_datetime(post.get("published_at"))
        or datetime.min.replace(tzinfo=UTC)
    )
    return CoverageResult(posts=kept_posts, stats=stats)


def detect_special_terms(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag comment-only bursts of marked terms (possible irony / in-group slang).
    水军/机器人矩阵、高频异常用词记录到 Memory 辅助。
    """
    post_counter: Counter[str] = Counter()
    comment_counter: Counter[str] = Counter()
    comment_examples: dict[str, str] = {}
    for post in posts:
        for token in _TOKEN_RE.findall(item_text(post)):
            post_counter[token] += 1
        comments = post.get("comments")
        if not isinstance(comments, list):
            continue
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            text = item_text(comment)
            for token in _TOKEN_RE.findall(text):
                comment_counter[token] += 1
                comment_examples.setdefault(token, text[:120])
    flagged: list[dict[str, Any]] = []
    for term in _MARKED_TERMS:
        in_comments = comment_counter.get(term, 0)
        in_posts = post_counter.get(term, 0)
        if in_comments >= 3 and in_comments >= in_posts * 3:
            flagged.append(
                {
                    "term": term,
                    "comment_count": in_comments,
                    "post_count": in_posts,
                    "hint": "评论区高频、主贴少见，语义可能偏离字面（反讽/黑话/站队）",
                    "example": comment_examples.get(term, ""),
                }
            )
    # 非词表：评论里出现 ≥5 次、主贴 0 次的 2–4 字中文词。
    for term, count in comment_counter.most_common(30):
        if term in _MARKED_TERMS:
            continue
        if not re.fullmatch(r"[\u4e00-\u9fff]{2,4}", term):
            continue
        if count >= 5 and post_counter.get(term, 0) == 0:
            flagged.append(
                {
                    "term": term,
                    "comment_count": count,
                    "post_count": 0,
                    "hint": "仅评论区聚集，可能是事件黑话或反讽用法",
                    "example": comment_examples.get(term, ""),
                }
            )
    return flagged[:12]


def format_coverage_memory(topic: str, stats: CoverageStats) -> str:
    lines = [
        f"采集覆盖统计（主题：{topic}）。平台搜索结果经时间后过滤并按天分桶，"
        f"每天每平台最多 {stats.per_day_limit} 条；",
        f"每帖评论最多 {stats.comment_limit} 条，均为过滤排序后的前列。",
        "平台搜索接口不保证完整历史覆盖；空窗表示本次结果未命中，不代表当日没有相关内容。",
        f"区间天数 {len(stats.days)}，空窗 {len(stats.empty_days)} 天"
        + (f"（{', '.join(stats.empty_days[:8])}）" if stats.empty_days else "")
        + "。",
        (
            f"评论 raw={stats.comment_raw} kept={stats.comment_kept} "
            f"短文本丢弃={stats.comment_dropped_short} "
            f"近重复丢弃={stats.comment_dropped_duplicate}。"
        ),
    ]
    for bucket in stats.buckets:
        if bucket.raw_count == 0 and bucket.kept == 0:
            continue
        lines.append(
            f"{bucket.day} / {bucket.platform}: raw={bucket.raw_count} "
            f"kept={bucket.kept} 短={bucket.dropped_short} "
            f"近重复={bucket.dropped_duplicate}（组 {bucket.duplicate_groups}） "
            f"相似度较高重复={bucket.similar_groups} 超限截断={bucket.dropped_other}"
        )
    if stats.special_terms:
        lines.append("评论区异常用词：")
        for item in stats.special_terms:
            lines.append(
                f"- 「{item['term']}」评论 {item['comment_count']} / 主贴 {item['post_count']}："
                f"{item['hint']}"
            )
    return "\n".join(lines)


def shannon_evenness(counts: list[int]) -> float:
    """0–1 evenness across day buckets; used as a continuity supplement."""
    positive = [count for count in counts if count > 0]
    if len(positive) <= 1:
        return 1.0 if positive else 0.0
    total = sum(positive)
    entropy = -sum((count / total) * math.log(count / total) for count in positive)
    return round(entropy / math.log(len(positive)), 4)
