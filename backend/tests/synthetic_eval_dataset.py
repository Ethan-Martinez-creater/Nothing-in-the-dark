"""M10: deterministic synthetic labelled datasets for domain evaluation.

Every label below is derivable from the classifier dictionaries in
``app/services/classifiers.py`` or from the propagation algorithm's
entity/time rules — the datasets measure real behaviour (negation
flips, intensifier amplification, exception phrases, time windows,
edge relations) rather than mirroring the implementation blindly.

The corpora are plain module constants/functions so tests share one
source of truth and the numbers stay reproducible across runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.propagation_algorithm import EdgeCandidate

# ---------------------------------------------------------------------------
# Sentiment corpus: (text, expected label)
# ---------------------------------------------------------------------------

SENTIMENT_CORPUS: list[tuple[str, str]] = [
    # plain polarity hits
    ("这款产品质量出色，值得推荐", "positive"),
    ("服务很贴心，好评如潮", "positive"),
    ("这个骗局令人愤怒", "negative"),
    ("严重欺骗消费者，简直是垃圾", "negative"),
    # intensifier amplification
    ("非常满意，效果显著", "positive"),
    ("太坑人了，完全是大骗局", "negative"),
    # negation flips polarity
    ("不是好评，而是差评", "negative"),
    ("并非差评，整体还行", "positive"),
    # "非常" exception: 非 inside 非常 must not flip
    ("非常满意这次服务", "positive"),
    # no lexicon hits -> neutral
    ("会议于下午三点召开", "neutral"),
    ("大家对这件事有什么看法？", "neutral"),
]

# ---------------------------------------------------------------------------
# Stance corpus: (text, expected label)
# ---------------------------------------------------------------------------

STANCE_CORPUS: list[tuple[str, str]] = [
    ("我支持这个方案", "supportive"),
    ("说得好，力挺你", "supportive"),
    ("坚决反对这种做法", "opposing"),
    ("强烈谴责这种乱象", "opposing"),
    ("这个结论真的靠谱吗？", "questioning"),
    ("怎么证明你说的都是真的", "questioning"),
    ("先观察后续进展", "neutral"),
    ("明天再说吧", "neutral"),
]

# ---------------------------------------------------------------------------
# Propagation posts: chain p1 -> p2 (inferred) -> p3 (observed retweet of p1)
# ---------------------------------------------------------------------------

PROPAGATION_POSTS: list[dict[str, Any]] = [
    # All three chain posts share exactly one entity ("80%") so entity
    # overlap is 1.0 between them; full dates are avoided because the
    # long-number pattern would split "2026-08-01" into extra entities
    # and dilute the overlap below the confidence threshold.
    {
        "id": "p1",
        "content": "XX 公司发布八月财报，营收增长 80%",
        "published_at": datetime(2026, 8, 1, 8, 0, tzinfo=UTC),
        "author": "媒体号A",
    },
    {
        "id": "p2",
        "content": "看了 80% 的增幅，数据很亮眼",
        "published_at": datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
        "author": "分析号B",
    },
    {
        "id": "p3",
        "content": "转发:XX 公司财报 80%",
        "published_at": datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        "author": "普通用户C",
        "raw": {"retweet_of_id": "p1"},
    },
    # No entities, no relation: must produce no edges.
    {
        "id": "p4",
        "content": "晚上吃什么好呢",
        "published_at": datetime(2026, 8, 2, 9, 0, tzinfo=UTC),
        "author": "路人D",
    },
    # Same entity but outside the 168h window: the algorithm must refuse.
    {
        "id": "p5",
        "content": "还记得那 80% 吗",
        "published_at": datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
        "author": "考古号E",
    },
]

PROPAGATION_EXPECTED: set[frozenset[str]] = {
    frozenset({"p1", "p2"}),
    frozenset({"p1", "p3"}),
    frozenset({"p2", "p3"}),
}

# ---------------------------------------------------------------------------
# Claim cards / report fixtures (verify_claims output shape)
# ---------------------------------------------------------------------------

CLAIM_CARDS: list[dict[str, Any]] = [
    {
        "id": "claim-1",
        "verdict": "insufficient",
        "supporting_evidence": ["ev-1", "post-1"],
        "contradicting_evidence": [],
    },
    {
        "id": "claim-2",
        "verdict": "credible",
        "supporting_evidence": ["ev-2"],
        "contradicting_evidence": ["post-9"],  # dangling reference
    },
    {
        "id": "claim-3",
        "verdict": "insufficient",
        "supporting_evidence": [],
        "contradicting_evidence": [],
    },
]

KNOWN_EVIDENCE_IDS: set[str] = {"ev-1", "ev-2", "post-1", "post-2"}

REFUSAL_CARDS: list[dict[str, Any]] = [
    {"verdict": "insufficient", "supporting_evidence": []},  # correct refusal
    {"verdict": "credible", "supporting_evidence": []},       # over-claim
    {"verdict": "credible", "supporting_evidence": ["ev-1"]},  # has evidence
    {"verdict": "old_news", "supporting_evidence": []},       # refusal
]

REPORTS: list[tuple[dict[str, Any], set[str]]] = [
    ({"citation_links": ["ev-1", "ev-2", "ghost-3"]}, {"ev-1", "ev-2"}),
    ({"citation_links": []}, {"ev-1", "ev-2"}),
    ({"citation_links": ["ev-1", "post-1"]}, {"ev-1", "ev-2", "post-1"}),
]

# ---------------------------------------------------------------------------
# Origin-candidate ranking: earliest post + two hubs, late noise excluded
# ---------------------------------------------------------------------------

_ORIGIN_BASE = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def _origin_post(post_id: str, offset_hours: int) -> dict[str, Any]:
    return {
        "id": post_id,
        "author": "作者",
        "platform": "weibo",
        "content": f"内容 {post_id}",
        "published_at": (_ORIGIN_BASE + timedelta(hours=offset_hours)).isoformat(),
    }


def _origin_edge(source: str, target: str) -> EdgeCandidate:
    return EdgeCandidate(
        source_post_id=source,
        target_post_id=target,
        relation="inferred",
        confidence=0.5,
        feature_scores={},
        reasons=["评测"],
        evidence_ids=[source, target],
    )


ORIGIN_POSTS: list[dict[str, Any]] = [
    _origin_post("s1", 0),
    _origin_post("h1", 1),
    _origin_post("h1-child-0", 2),
    _origin_post("h1-child-1", 2),
    _origin_post("h1-child-2", 2),
    _origin_post("h2", 1),
    _origin_post("h2-child-0", 2),
    _origin_post("h2-child-1", 2),
    _origin_post("h2-child-2", 2),
    _origin_post("noise1", 48),
    _origin_post("noise2", 49),
]

ORIGIN_EDGES: list[EdgeCandidate] = [
    _origin_edge("h1", "h1-child-0"),
    _origin_edge("h1", "h1-child-1"),
    _origin_edge("h1", "h1-child-2"),
    _origin_edge("h2", "h2-child-0"),
    _origin_edge("h2", "h2-child-1"),
    _origin_edge("h2", "h2-child-2"),
]

ORIGIN_RELEVANT: set[str] = {"s1", "h1", "h2"}
