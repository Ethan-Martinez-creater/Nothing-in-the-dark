"""叙事生命周期与纠错传播评估（10）。

- NarrativeClusterer：时间约束的增量聚类（bigram Jaccard + 平台/时间特征），
  聚类版本不可原地覆盖，新内容可加入 / 形成变体 / 创建新叙事。
- LifecycleAnalyzer：固定时间桶内的量/账号/互动/跨平台数，平滑增长率 +
  峰值显著性 + 最小持续窗口判定阶段；数据缺口输出 unknown（不制造衰退）。
- CorrectionAnalyzer：纠错事件的描述性前后对比，默认不声称因果。

“最早采集到”不等于真实世界绝对源头；字段与文案严格区分。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

STAGES = ("emerging", "growing", "peaking", "declining", "resurgent", "dormant", "unknown")

_ALGORITHM_VERSION = "narrative-1.0.0"

# 短公共文本 / 模板信号（平台固定文案等降权）。
_TEMPLATE_MARKERS = (
    "转发了微博",
    "分享视频",
    "发布了一篇文章",
    "上传了视频",
    "发起投票",
    "赞了",
    "关注了",
)


def _bigrams(text: str) -> set[str]:
    text = re.sub(r"[\s\W]+", "", text or "")
    return {text[i : i + 2] for i in range(max(0, len(text) - 1))}


def jaccard(left: str, right: str) -> float:
    a = _bigrams(left)
    b = _bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _is_template(text: str) -> bool:
    return any(marker in (text or "") for marker in _TEMPLATE_MARKERS)


@dataclass(slots=True)
class NarrativeCandidate:
    narrative_id: str
    title: str
    centroid: set[str]
    members: list[dict[str, Any]] = field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    def keywords(self, top_k: int = 8) -> list[str]:
        counter: Counter[str] = Counter()
        for member in self.members:
            text = str(member.get("content") or "")
            for gram in _bigrams(text):
                counter[gram] += 1
        return [word for word, _ in counter.most_common(top_k)]


class NarrativeClusterer:
    """时间约束增量聚类：新内容加入现有叙事或创建新叙事。"""

    def __init__(
        self,
        *,
        similarity_threshold: float = 0.32,
        template_weight: float = 0.4,
        watermark: datetime | None = None,
    ) -> None:
        self._threshold = similarity_threshold
        self._template_weight = template_weight
        self._watermark = watermark or datetime.now(UTC)
        self._candidates: dict[str, NarrativeCandidate] = {}

    def cluster(self, posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """对一批帖子做增量聚类；返回叙事候选快照（可持久化为版本）。"""
        for post in posts:
            self._assign(post)
        result: list[dict[str, Any]] = []
        for candidate in self._candidates.values():
            result.append(
                {
                    "narrative_id": candidate.narrative_id,
                    "title": candidate.title,
                    "keywords": candidate.keywords(),
                    "member_count": len(candidate.members),
                    "first_seen": (
                        candidate.first_seen.isoformat() if candidate.first_seen else None
                    ),
                    "last_seen": (
                        candidate.last_seen.isoformat() if candidate.last_seen else None
                    ),
                    "members": candidate.members,
                    "algorithm_version": _ALGORITHM_VERSION,
                }
            )
        return result

    def _assign(self, post: dict[str, Any]) -> None:
        content = str(post.get("content") or post.get("title") or "")
        if not content or len(content) < 4:
            return
        if _is_template(content):
            weight = self._template_weight
        else:
            weight = 1.0
        best_id: str | None = None
        best_score = 0.0
        for candidate_id, candidate in self._candidates.items():
            centroid_text = " ".join(sorted(candidate.centroid)[:20])
            score = jaccard(content, centroid_text) * weight
            if score > best_score:
                best_score = score
                best_id = candidate_id
        if best_id is not None and best_score >= self._threshold:
            candidate = self._candidates[best_id]
            candidate.members.append(post)
            candidate.centroid.update(_bigrams(content))
            published = post.get("published_at")
            ts = self._parse_time(published)
            if ts is not None:
                if candidate.first_seen is None or ts < candidate.first_seen:
                    candidate.first_seen = ts
                if candidate.last_seen is None or ts > candidate.last_seen:
                    candidate.last_seen = ts
            return
        narrative_id = f"narr-{len(self._candidates) + 1}"
        candidate = NarrativeCandidate(
            narrative_id=narrative_id,
            title=content[:60],
            centroid=set(_bigrams(content)),
            members=[post],
        )
        ts = self._parse_time(post.get("published_at"))
        candidate.first_seen = ts
        candidate.last_seen = ts
        self._candidates[narrative_id] = candidate

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None


# ---------------------------------------------------------------------------
# 生命周期判定
# ---------------------------------------------------------------------------


def _smooth(series: list[float], window: int = 3) -> list[float]:
    if not series:
        return []
    smoothed: list[float] = []
    for i in range(len(series)):
        start = max(0, i - window + 1)
        chunk = series[start : i + 1]
        smoothed.append(sum(chunk) / len(chunk))
    return smoothed


def _stage_for_bucket(
    volume: float,
    *,
    prev_smoothed: float | None,
    next_volume: float | None,
    baseline_median: float,
    min_peak: float,
    dormant_after: int,
    active_bucket_count: int,
) -> str:
    """单桶阶段判定：需要 最小持续窗口 / 缺口 unknown / 复燃检测 的输入。"""
    if volume == 0 and prev_smoothed is None:
        return "unknown"  # 数据缺口：不自动判定衰退
    if active_bucket_count < dormant_after:
        return "unknown"
    if volume >= min_peak and prev_smoothed is not None:
        if volume >= max(min_peak, prev_smoothed * 2.0):
            return "peaking" if volume >= min_peak * 2 else "growing"
        return "growing"
    if prev_smoothed is None:
        return "emerging"
    if volume < prev_smoothed and prev_smoothed > 0:
        return "declining"
    return "dormant"


class LifecycleAnalyzer:
    """时间桶序列阶段判定：平滑增长率 / 峰值 / 最小持续窗口 / 缺口处理。"""

    def __init__(
        self,
        *,
        bucket_seconds: int = 3600,
        min_peak: int = 3,
        dormant_after_buckets: int = 6,
        resurgence_ratio: float = 2.0,
    ) -> None:
        self._bucket_seconds = bucket_seconds
        self._min_peak = min_peak
        self._dormant_after = dormant_after_buckets
        self._resurgence_ratio = resurgence_ratio

    def analyze(
        self,
        timeline: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """timeline: [{bucket, platform, volume, unique_accounts, engagement}]。

        返回阶段序列 + 说明（数据缺口/复燃/峰值均明确标注）。
        """
        by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for point in timeline:
            by_bucket[str(point["bucket"])].append(point)
        ordered_buckets = sorted(by_bucket)
        series = [
            sum(int(p.get("volume") or 0) for p in by_bucket[bucket])
            for bucket in ordered_buckets
        ]
        if not series:
            return {"stages": [], "buckets": [], "notes": []}
        smoothed = _smooth([float(v) for v in series])
        baseline = median(series) or 1.0
        stages: list[str] = []
        notes: list[str] = []
        active_buckets = sum(1 for v in series if v > 0)
        for i, bucket in enumerate(ordered_buckets):
            prev_smoothed = smoothed[i - 1] if i > 0 else None
            next_volume = series[i + 1] if i + 1 < len(series) else None
            stage = _stage_for_bucket(
                float(series[i]),
                prev_smoothed=prev_smoothed,
                next_volume=next_volume,
                baseline_median=baseline,
                min_peak=float(self._min_peak),
                dormant_after=self._dormant_after,
                active_bucket_count=active_buckets,
            )
            if stage == "peaking":
                notes.append(f"峰值桶 {bucket}（volume={series[i]}）")
            if stage == "unknown" and series[i] == 0:
                notes.append(f"数据缺口 {bucket}：不判定为衰退")
            stages.append(stage)
        # 复燃检测：休眠桶（0/低量）之后再次超过基线的 resurgence_ratio 倍。
        resurgent_indexes = self._detect_resurgence(series, baseline)
        for idx in resurgent_indexes:
            if idx < len(ordered_buckets):
                stages[idx] = "resurgent"
                notes.append(f"复燃桶 {ordered_buckets[idx]}（超过基线 x{self._resurgence_ratio}）")
        return {
            "stages": stages,
            "buckets": ordered_buckets,
            "series": series,
            "smoothed": [round(v, 2) for v in smoothed],
            "notes": notes,
            "algorithm_version": _ALGORITHM_VERSION,
        }

    def _detect_resurgence(self, series: list[float], baseline: float) -> list[int]:
        indexes: list[int] = []
        dormant_window = 0
        for i, value in enumerate(series):
            if value == 0:
                dormant_window += 1
            else:
                if (
                    dormant_window >= 2
                    and baseline > 0
                    and value >= baseline * self._resurgence_ratio
                ):
                    indexes.append(i)
                dormant_window = 0
        return indexes


# ---------------------------------------------------------------------------
# 纠错影响分析
# ---------------------------------------------------------------------------


class CorrectionAnalyzer:
    """纠错事件描述性前后对比；默认不声称因果。"""

    def analyze(
        self,
        *,
        correction_time: datetime,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        method: str = "descriptive",
    ) -> dict[str, Any]:
        before_volume = len(before)
        after_volume = len(after)
        before_engagement = sum(
            int(p.get("engagement") or 0) for p in before
        )
        after_engagement = sum(int(p.get("engagement") or 0) for p in after)
        metrics = {
            "before_volume": before_volume,
            "after_volume": after_volume,
            "volume_change": after_volume - before_volume,
            "before_engagement": before_engagement,
            "after_engagement": after_engagement,
            "engagement_change": after_engagement - before_engagement,
        }
        result = (
            "纠错后描述性对比：无显著结论（样本不足或时间窗口过短）"
            if before_volume + after_volume < 4
            else (
                "纠错后帖子/互动量出现变化（描述性），不构成因果证明"
            )
        )
        return {
            "window": {
                "before_start": (correction_time - timedelta(hours=24)).isoformat(),
                "correction_at": correction_time.isoformat(),
                "after_end": (correction_time + timedelta(hours=24)).isoformat(),
            },
            "method": method,
            "metrics": metrics,
            "limitations": [
                "描述性前后对比：时间先后不等于因果",
                "未控制平台算法、突发事件与外部因素",
                "纠错内容本身需人工或外部证据确认",
            ],
            "result": result,
            "confidence_level": "low",
            "causal_claim": False,
        }


def first_seen_vs_origin_label(first_seen: datetime | None) -> str:
    """“最早采集到”与“绝对源头”的字段区分。"""
    if first_seen is None:
        return "unknown"
    return "first_collected"  # 永远不输出 “absolute_origin”
