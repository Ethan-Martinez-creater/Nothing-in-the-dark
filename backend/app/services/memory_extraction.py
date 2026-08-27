"""Memory 生命周期：候选提取、重要性判断、相似去重与衰减判定。

规则基实现（不依赖 LLM，便于测试与离线运行）：显式指令/偏好/纠正模式
提取候选；重要性由类别决定；相似度用字符 bigram Dice 系数做去重与
自动纠正；衰减判定按活跃时长与重要性阈值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# ---------- 候选提取 ----------

_CORRECTION_PATTERNS = (
    re.compile(r"(不对|错了|不是这样|搞错|更正|纠正|应该是|实际上)"),
    re.compile(r"(之前说的|上一条).{0,20}(作废|无效|撤回|不对)"),
)
_INSTRUCTION_PATTERNS = (
    re.compile(r"(请记住|记住|务必记住|请记录|记录下来|以后都要|每次都|一律)"),
    re.compile(r"(优先|重点关注|务必|一定不要|千万不要|禁止)"),
)
_PREFERENCE_PATTERNS = (
    re.compile(r"(我喜欢|偏好|更喜欢|倾向于|希望|需要你)"),
    re.compile(r"(用中文|简体|正式一点|简洁|详细一些|表格形式)"),
)


@dataclass(slots=True)
class MemoryCandidate:
    content: str
    kind: str  # correction / constraint / preference（对齐 CreateMemoryRequest）
    importance: float
    pattern: str


_IMPORTANCE = {
    "correction": 0.95,
    "constraint": 0.85,
    "preference": 0.7,
}


def extract_memory_candidates(text: str) -> list[MemoryCandidate]:
    """从对话文本提取值得持久化的 Memory 候选（按出现顺序去重）。"""
    candidates: list[MemoryCandidate] = []
    seen: set[str] = set()
    for sentence in _split_sentences(text):
        sentence = sentence.strip()
        if len(sentence) < 6 or sentence in seen:
            continue
        for kind, patterns in (
            ("correction", _CORRECTION_PATTERNS),
            ("constraint", _INSTRUCTION_PATTERNS),
            ("preference", _PREFERENCE_PATTERNS),
        ):
            for pattern in patterns:
                if pattern.search(sentence):
                    candidates.append(
                        MemoryCandidate(
                            content=sentence,
                            kind=kind,
                            importance=_IMPORTANCE[kind],
                            pattern=pattern.pattern,
                        )
                    )
                    seen.add(sentence)
                    break
            if sentence in seen:
                break
    return candidates


def _split_sentences(text: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"[。！？!?；;\n]+", text)
        if part.strip()
    ]


# ---------- 相似去重 ----------

def text_similarity(left: str, right: str) -> float:
    """字符 bigram 集合的 Dice 系数，0~1。"""
    def bigrams(value: str) -> set[str]:
        chars = "".join(value.split())
        return {chars[i : i + 2] for i in range(len(chars) - 1)}

    left_grams = bigrams(left)
    right_grams = bigrams(right)
    if not left_grams or not right_grams:
        return 0.0
    overlap = len(left_grams & right_grams)
    return 2 * overlap / (len(left_grams) + len(right_grams))


def find_similar(
    existing: list[str],
    content: str,
    threshold: float,
) -> tuple[int, float] | None:
    """返回与 content 最相似的既有文本 (index, score)，低于阈值返回 None。"""
    best_index = -1
    best_score = 0.0
    for index, item in enumerate(existing):
        score = text_similarity(item, content)
        if score > best_score:
            best_index = index
            best_score = score
    if best_score >= threshold:
        return best_index, best_score
    return None


def _keywords(text: str) -> set[str]:
    """去空白后的字符 bigram 集合（无分词依赖，主题相关片段天然共享）。"""
    chars = "".join(text.split())
    return {chars[i : i + 2] for i in range(len(chars) - 1)}


def find_related(
    existing: list[str],
    content: str,
    threshold: float,
) -> tuple[int, float] | None:
    """按 bigram Dice 系数找主题相关的既有文本（用于纠正类覆盖，
    即使整句相似度低也能命中同一主题的旧值）。"""
    content_grams = _keywords(content)
    if not content_grams:
        return None
    best_index = -1
    best_score = 0.0
    for index, item in enumerate(existing):
        item_grams = _keywords(item)
        if not item_grams:
            continue
        overlap = len(content_grams & item_grams)
        score = 2 * overlap / (len(content_grams) + len(item_grams))
        if score > best_score:
            best_index = index
            best_score = score
    if best_index >= 0 and best_score >= threshold:
        return best_index, best_score
    return None


# ---------- 衰减判定 ----------

def _utc(value: datetime) -> datetime:
    """SQLite 返回 naive datetime，统一按 UTC 处理再比较。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def should_decay(
    updated_at: datetime,
    importance: float,
    *,
    now: datetime,
    ttl_days: int,
    min_importance: float,
) -> bool:
    """超过 TTL 且重要性低于阈值的 Memory 建议失效。"""
    return (
        importance < min_importance
        and (_utc(now) - _utc(updated_at)) >= timedelta(days=ttl_days)
    )
