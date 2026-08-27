"""Statistical and extraction helpers used by the domain expert agents.

These functions only compute what the data actually supports: real
sentiment/stance classification, claim extraction from the bounded
sample, and propagation edges from explicit relations plus semantic
signals. Narrative conclusions are the job of the expert agents (LLM)
grounded in evidence; nothing here may emit fixed opinion text unrelated
to the input posts.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.services import opinion_analysis, propagation_algorithm
from app.services.classifiers import ModelClassification, ModelSentimentClassifier

if TYPE_CHECKING:
    from app.application.repositories import ApplicationRepository
    from app.infrastructure.embeddings import EmbeddingWorkerClient

# Minimum / maximum claim length after sentence splitting.
_CLAIM_MIN_LENGTH = 8
_CLAIM_MAX_LENGTH = 200
_CLAIM_CAP = 15

# Verbs that mark a statement as a verifiable claim.
_CLAIM_VERBS = (
    "称",
    "表示",
    "指出",
    "宣布",
    "曝光",
    "爆料",
    "发布",
    "证实",
    "否认",
    "回应",
    "声称",
    "质疑",
)


def analyze_opinion(
    posts: list[dict[str, Any]],
    *,
    classifications: list[ModelClassification] | None = None,
    include_clusters: bool = True,
) -> dict[str, Any]:
    """Real sentiment / stance / intensity / platform distributions.

    ``classifications`` must align with ``posts`` (model-first batch
    results). When missing, every post is classified with the dictionary
    classifier instead of trusting a ``post["sentiment"]`` field.

    With ``include_clusters``, M7b intelligence is appended: cosine
    K-Means opinion groups with TF-IDF themes, hourly volume/sentiment
    series with trend breakpoints and an influencer ranking. All keys are
    computed from the data only; no narrative text is generated.
    """
    if classifications is None:
        classifications = [
            ModelSentimentClassifier.classify_dictionary(
                str(post.get("content") or "")
            )
            for post in posts
        ]
    if len(classifications) != len(posts):
        raise ValueError(
            "classifications must align with posts "
            f"({len(classifications)} vs {len(posts)})"
        )
    sentiment_counts = Counter(item.sentiment for item in classifications)
    stance_counts = Counter(item.stance for item in classifications)
    platform_counts = Counter(str(post["platform"]) for post in posts)
    intensity_counts = Counter(
        _intensity_band(item.score) for item in classifications
    )
    total = max(len(posts), 1)
    distribution = {
        label: round(sentiment_counts.get(label, 0) / total * 100, 1)
        for label in ("positive", "neutral", "negative")
    }
    result: dict[str, Any] = {
        "statistics": {
            "total_posts": len(posts),
            "sentiment_distribution": distribution,
            "stance_distribution": {
                label: round(stance_counts.get(label, 0) / total * 100, 1)
                for label in ("supportive", "opposing", "questioning", "neutral")
            },
            "intensity_distribution": {
                label: round(intensity_counts.get(label, 0) / total * 100, 1)
                for label in ("strong", "moderate", "weak")
            },
            "platform_distribution": dict(platform_counts),
        },
        # Backwards-compatible keys for the legacy analysis graph.
        "sentiment_distribution": distribution,
        "platform_distribution": dict(platform_counts),
        "is_demo": any(bool(post.get("is_demo")) for post in posts),
    }
    if include_clusters and posts:
        texts = [str(post.get("content") or "") for post in posts]
        sentiments = [item.sentiment for item in classifications]
        series = opinion_analysis.hourly_series(posts, sentiments)
        result["clusters"] = opinion_analysis.opinion_groups(posts, texts)
        result["time_series"] = series
        result["trends"] = opinion_analysis.trend_breakpoints(series)
        result["influencers"] = opinion_analysis.influencer_ranking(posts)
        result["explanation"] = opinion_analysis.explain_opinion_statistics(
            result, posts
        )
    return result


def _intensity_band(score: float) -> str:
    magnitude = abs(score)
    if magnitude >= 0.5:
        return "strong"
    if magnitude >= 0.15:
        return "moderate"
    return "weak"


async def reconstruct_propagation(
    posts: list[dict[str, Any]],
    *,
    embedding_client: EmbeddingWorkerClient | None = None,
    llm: Any = None,
) -> dict[str, Any]:
    """Propagation graph from the v1.0.0 candidate-edge algorithm.

    Observed edges come from explicit platform relations only; inferred
    edges combine time decay, BGE-M3 text similarity and entity overlap.
    """
    return await propagation_algorithm.reconstruct_propagation(
        posts,
        embedding_client=embedding_client,
        llm=llm,
    )


def extract_claim_candidates(
    posts: list[dict[str, Any]],
    topic: str,
) -> list[dict[str, Any]]:
    """Heuristic claim extraction: split sentences, keep verifiable ones.

    Sentences carrying a claim verb, date or entity pattern score higher
    and are kept first; short chit-chat and empty content are dropped.
    """
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for post in posts:
        source_post_id = str(post.get("id"))
        content = str(post.get("content") or "")
        for sentence in re.split(r"[。！？!?\n]+", content):
            sentence = " ".join(sentence.split())
            if not (_CLAIM_MIN_LENGTH <= len(sentence) <= _CLAIM_MAX_LENGTH):
                continue
            normalized = sentence.strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            score = _claim_score(normalized, topic)
            candidates.append(
                {
                    "text": normalized,
                    "source_post_id": source_post_id,
                    "score": score,
                }
            )
    candidates.sort(key=lambda item: -item["score"])
    return candidates[:_CLAIM_CAP]


def _normalize_claim_key(text: str) -> str:
    """Canonical key for claim dedup: NFKC, lowercased, alnum only.

    Sentences that differ only in full-width/half-width punctuation, casing
    or whitespace collapse to the same key, so a claim repeated across posts
    (or across platforms) is counted once.
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFKC", text).lower() if ch.isalnum()
    )


def _claim_score(text: str, topic: str) -> int:
    score = 0
    if any(verb in text for verb in _CLAIM_VERBS):
        score += 3
    if any(pattern.search(text) for pattern in propagation_algorithm.ENTITY_PATTERNS):
        score += 2
    if topic and topic in text:
        score += 1
    return score


async def verify_claims(
    posts: list[dict[str, Any]],
    topic: str,
    *,
    repository: ApplicationRepository | None = None,
    case_id: str | None = None,
    created_by_run_id: str | None = None,
) -> dict[str, Any]:
    """Extract candidate claims and optionally persist them to ``claims``.

    M7d verification rules run on top of the persisted evidence:
    * old-news reuse: a claim whose source post predates the case
      ``time_range.start`` by more than a day is flagged ``old_news``
      (retelling of an old event as if new);
    * authoritative source: when the source account is on the whitelist
      (``accounts.is_authoritative``) the card becomes ``credible`` with a
      bounded confidence, never ``true``;
    * otherwise the verdict stays ``insufficient`` — insufficient evidence
      forces a non-verdict, matching the Evidence Critic's floor rule.

    Without a repository the old-news check is skipped and the verdict is
    always ``insufficient`` (the Verification Agent (LLM) decides real
    verdicts from evidence it gathers separately).
    """
    candidates = extract_claim_candidates(posts, topic)
    posts_by_id = {str(post.get("id")): post for post in posts}
    # Normalized dedup: sentences differing only in full/half-width
    # punctuation, casing or whitespace are the same claim; keep the first
    # occurrence (extract already sorts by claim score).
    key_map: dict[str, dict[str, Any]] = {}
    deduped = 0
    for candidate in candidates:
        key = _normalize_claim_key(str(candidate["text"]))
        if key in key_map:
            deduped += 1
            continue
        key_map[key] = candidate
    unique_candidates = list(key_map.values())
    # Cross-platform agreement index: the set of platforms carrying each
    # normalized claim key across the whole sample.
    platforms_by_key: dict[str, set[str]] = {}
    for post in posts:
        content = str(post.get("content") or "")
        platform = str(post.get("platform") or "")
        for sentence in re.split(r"[。！？!?\n]+", content):
            sentence = " ".join(sentence.split())
            if not (_CLAIM_MIN_LENGTH <= len(sentence) <= _CLAIM_MAX_LENGTH):
                continue
            platforms_by_key.setdefault(
                _normalize_claim_key(sentence.strip()), set()
            ).add(platform)
    # M7d: case window + authoritative whitelist, loaded once.
    case_start: datetime | None = None
    authoritative_names: set[str] = set()
    if repository is not None and case_id:
        case = await repository.get_case(case_id)
        raw_start = (case.time_range or {}).get("start")
        if raw_start:
            try:
                parsed_start = datetime.fromisoformat(str(raw_start))
                # 时间范围存的是裸日期（如 2026-07-16），fromisoformat
                # 解析为 naive datetime；帖子时间均为 UTC aware，
                # 直接相减会抛 offset-naive/aware 异常。
                case_start = (
                    parsed_start.replace(tzinfo=UTC)
                    if parsed_start.tzinfo is None
                    else parsed_start
                )
            except ValueError:
                case_start = None
        authoritative = await repository.list_authoritative_accounts()
        authoritative_names = {
            str(account.name or "") for account in authoritative
        } | {str(account.normalized_name or "") for account in authoritative}

    cards: list[dict[str, Any]] = []
    can_persist = bool(repository and case_id and created_by_run_id)
    check_totals: Counter[str] = Counter()
    for index, candidate in enumerate(unique_candidates):
        claim_id: str | None = None
        if can_persist:
            assert repository is not None and case_id and created_by_run_id
            record = await repository.create_claim(
                case_id=case_id,
                text=candidate["text"],
                created_by_run_id=created_by_run_id,
            )
            claim_id = record.id
            # The claim's source post becomes its first candidate evidence
            # (context stance); idempotent per (case, source, claim).
            await repository.create_evidence(
                case_id=case_id,
                claim_id=claim_id,
                source_type="post",
                source_id=candidate["source_post_id"],
                stance="context",
                excerpt=candidate["text"],
                relevance=0.5,
            )
        source = posts_by_id.get(candidate["source_post_id"]) or {}
        checks, verdict, verdict_label, confidence, reason = (
            _verify_card(candidate, source, case_start, authoritative_names)
        )
        key = _normalize_claim_key(str(candidate["text"]))
        claim_platforms = platforms_by_key.get(key, set())
        if len(claim_platforms) >= 2:
            checks.append("cross_platform")
            confidence = min(confidence + 0.05, 0.9)
            reason = (
                f"{reason} 同一主张在 {len(claim_platforms)} 个平台出现，"
                "跨平台语境一致，置信度小幅上调。"
            )
        consistency = assess_claim_consistency(candidate, source, topic)
        for name, status in consistency.items():
            if status == "fail":
                if name == "temporal_consistency":
                    checks.append("temporal_mismatch")
                else:
                    checks.append(name)
        check_totals.update(checks)
        check_totals[f"temporal_consistency:{consistency['temporal_consistency']}"] += 1
        check_totals[f"subject_consistency:{consistency['subject_consistency']}"] += 1
        check_totals[f"context_consistency:{consistency['context_consistency']}"] += 1
        cards.append(
            {
                "id": claim_id or f"claim-{index + 1}",
                "claim": candidate["text"],
                "verdict": verdict,
                "verdict_label": verdict_label,
                "confidence": confidence,
                "reason": reason,
                "supporting_evidence": [candidate["source_post_id"]],
                "contradicting_evidence": [],
                "source_post_id": candidate["source_post_id"],
                "checks": checks,
                **consistency,
            }
        )
    return {
        "cards": cards,
        "method": "social-evidence-only",
        "claim_extraction": {
            "algorithm_version": propagation_algorithm.ALGORITHM_VERSION,
            "persisted": can_persist,
            "candidate_count": len(unique_candidates),
            "dedup_normalization": "nfkc_alnum",
        },
        "verification_checks": {
            "old_news": check_totals.get("old_news", 0),
            "authoritative_source": check_totals.get("authoritative_source", 0),
            "insufficient": check_totals.get("insufficient", 0),
            "deduped": deduped,
            "cross_platform": check_totals.get("cross_platform", 0),
            "temporal_consistency": {
                "pass": check_totals.get("temporal_consistency:pass", 0),
                "fail": check_totals.get("temporal_consistency:fail", 0),
                "unknown": check_totals.get("temporal_consistency:unknown", 0),
            },
            "subject_consistency": {
                "pass": check_totals.get("subject_consistency:pass", 0),
                "fail": check_totals.get("subject_consistency:fail", 0),
                "unknown": check_totals.get("subject_consistency:unknown", 0),
            },
            "context_consistency": {
                "pass": check_totals.get("context_consistency:pass", 0),
                "fail": check_totals.get("context_consistency:fail", 0),
                "unknown": check_totals.get("context_consistency:unknown", 0),
            },
        },
        "notice": (
            f"候选主张 {len(cards)} 条均来自当前样本；未使用通用网页搜索，"
            "结论仅代表当前社交平台证据范围。"
        ),
        "is_demo": any(bool(post.get("is_demo")) for post in posts),
    }


_OLD_NEWS_LEAD_HOURS = 24.0
_CLAIM_DATE = re.compile(r"(20\d{2})[-/年](\d{1,2})(?:[-/月](\d{1,2}))?")


def assess_claim_consistency(
    candidate: dict[str, Any],
    source: dict[str, Any],
    topic: str,
) -> dict[str, str]:
    """Compare a claim sentence to its source post and the case topic.

    Returns pass/fail/unknown for temporal, subject and context axes.
    """
    text = str(candidate.get("text") or "")
    content = str(source.get("content") or "")
    context = "pass" if text and text in content else ("unknown" if not content else "fail")

    temporal = "unknown"
    match = _CLAIM_DATE.search(text)
    published = propagation_algorithm._post_datetime(source)  # noqa: SLF001
    if match and published is not None:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3) or 1)
        try:
            claimed = datetime(year, month, day, tzinfo=UTC)
        except ValueError:
            claimed = None
        if claimed is not None:
            # A claim dated more than a day before the post was published is
            # treated as a temporal mismatch (old event retold as current).
            delta_hours = (published - claimed).total_seconds() / 3600.0
            temporal = "fail" if delta_hours > 24 else "pass"
    elif not match:
        temporal = "unknown"

    claim_entities = propagation_algorithm.extract_entities(text)
    topic_entities = propagation_algorithm.extract_entities(topic)
    topic_tokens = re.findall(r"[\w一-鿿]{2,}", topic or "")
    # Subject is about the case topic, not the source post (source overlap
    # is already covered by context_consistency).
    if topic and any(token in text for token in topic_tokens):
        subject = "pass"
    elif claim_entities and topic_entities and claim_entities & topic_entities:
        subject = "pass"
    elif topic:
        subject = "fail"
    else:
        subject = "unknown"

    return {
        "temporal_consistency": temporal,
        "subject_consistency": subject,
        "context_consistency": context,
    }


def _verify_card(
    candidate: dict[str, Any],
    source: dict[str, Any],
    case_start: datetime | None,
    authoritative_names: set[str],
) -> tuple[list[str], str, str, float, str]:
    """Rule-based verification: old-news reuse > authoritative source >
    insufficient (forced non-verdict when evidence does not cover it)."""
    checks: list[str] = []
    author = str(source.get("author") or "")
    is_authoritative = (
        bool(author)
        and bool(authoritative_names)
        and (
            author in authoritative_names
            or propagation_algorithm.normalize_account_name(author)
            in {
                propagation_algorithm.normalize_account_name(name)
                for name in authoritative_names
            }
        )
    )
    if case_start is not None:
        published = propagation_algorithm._post_datetime(source)  # noqa: SLF001
        if (
            published is not None
            and (case_start - published).total_seconds() / 3600.0
            > _OLD_NEWS_LEAD_HOURS
        ):
            checks.append("old_news")
            return (
                checks,
                "old_news",
                "疑似旧闻新传",
                0.6,
                "该主张来源帖发布时间早于案例关注起点一天以上，疑似旧闻重发。",
            )
    if is_authoritative:
        checks.append("authoritative_source")
        return (
            checks,
            "credible",
            "官方来源",
            0.6,
            "该主张来自白名单官方账号，来源可信度高，但仍需结合证据复核。",
        )
    checks.append("insufficient")
    return (
        checks,
        "insufficient",
        "证据不足",
        0.5,
        "当前样本内无覆盖该主张的权威证据，结论待核验。",
    )


def build_report(
    topic: str,
    opinion: dict[str, Any],
    propagation: dict[str, Any],
    fact_check: dict[str, Any],
) -> dict[str, Any]:
    """Build a Report IR whose conclusions bind real Evidence IDs.

    ``citation_links`` are structured ``{conclusion, evidence_ids}`` objects
    so HTML export and the Citation Validator share one schema.
    """
    from app.schemas.reports import ReportCitation, ReportIR, ReportSection

    is_demo = bool(opinion.get("is_demo"))
    stats = opinion.get("statistics") or {}
    distribution = stats.get("platform_distribution") or {}
    platforms_text = "、".join(
        f"{platform} {count} 条"
        for platform, count in sorted(distribution.items(), key=lambda item: -item[1])
    ) or "无"
    explanation = opinion.get("explanation") or {}
    executive_summary = str(explanation.get("text") or "") or (
        f"当前样本共分析 {stats.get('total_posts', 0)} 条帖子，"
        f"平台分布：{platforms_text}。"
    )
    # 容错：专家可能把 get_artifact 的包装结构（{"artifact": {"data": …}}）
    # 而非 artifact.data 本身传入。
    if "artifact" in propagation and isinstance(propagation.get("artifact"), dict):
        inner = propagation["artifact"].get("data")
        if isinstance(inner, dict):
            propagation = inner
    nodes_count = len(propagation.get("nodes") or [])
    edges_count = len(propagation.get("edges") or [])
    origin_ids = [
        str(item.get("node_id"))
        for item in (propagation.get("origin_candidates") or [])
        if item.get("node_id")
    ]
    opinion_ids = list(explanation.get("evidence_ids") or [])
    cards = list(fact_check.get("cards") or [])
    fact_ids: list[str] = []
    citations: list[ReportCitation] = []
    for card in cards:
        ids = [
            str(item)
            for item in (card.get("supporting_evidence") or [])
            + (card.get("contradicting_evidence") or [])
            if item
        ]
        if card.get("source_post_id"):
            ids.append(str(card["source_post_id"]))
        # Preserve order, drop empties.
        unique = list(dict.fromkeys(ids))
        fact_ids.extend(unique)
        citations.append(
            ReportCitation(
                conclusion=str(card.get("claim") or card.get("verdict") or "核查结论"),
                evidence_ids=unique or ["unspecified"],
            )
        )
    if not citations and opinion_ids:
        citations.append(
            ReportCitation(conclusion=executive_summary, evidence_ids=opinion_ids)
        )
    if not citations:
        fallback = origin_ids or ["sample"]
        citations.append(
            ReportCitation(conclusion=executive_summary, evidence_ids=fallback)
        )

    def _ids(*groups: list[str]) -> list[str]:
        merged: list[str] = []
        for group in groups:
            for item in group:
                if item and item not in merged:
                    merged.append(item)
        return merged or ["sample"]

    report = ReportIR(
        title=f"{topic}：跨平台舆情分析简报",
        executive_summary=executive_summary,
        citation_links=citations,
        sections=[
            ReportSection(
                id="opinion",
                title="舆论概览",
                content=(
                    f"共 {stats.get('total_posts', 0)} 条帖子，"
                    f"情感分布 {opinion.get('sentiment_distribution')}。"
                ),
                evidence_ids=_ids(opinion_ids, [
                    str(cluster.get("representative_post_id"))
                    for cluster in (opinion.get("clusters") or [])
                    if cluster.get("representative_post_id")
                ]),
            ),
            ReportSection(
                id="propagation",
                title="传播链路",
                content=(
                    f"识别 {nodes_count} 个传播节点和 "
                    f"{edges_count} 条候选传播边"
                    f"（算法版本 {propagation.get('algorithm_version', 'unknown')}）。"
                ),
                evidence_ids=_ids(origin_ids),
            ),
            ReportSection(
                id="fact-check",
                title="事实核查",
                content=f"形成 {len(cards)} 张核查卡片。",
                evidence_ids=_ids(fact_ids),
            ),
        ],
        disclaimer=(
            "本报告使用确定性演示样本，不代表真实舆情结论。"
            if is_demo
            else "本报告仅覆盖当前采集到的社交平台样本；传播边与事实核查结论均需结合证据复核。"
        ),
        is_demo=is_demo,
        propagation_ref={
            "algorithm_version": propagation.get("algorithm_version"),
            "nodes": nodes_count,
            "edges": edges_count,
        },
        fact_check_summary={"card_count": len(cards)},
    )
    return report.model_dump()
