"""M7b opinion intelligence: clustering, themes, trends, influencers.

Pure standard library: character-bigram TF vectors with cosine distance,
kmeans++-initialized K-Means, TF-IDF theme naming, hourly time windows,
trend breakpoint detection and engagement-weighted influencer ranking.
No numpy / sklearn / PIL dependency (project convention).
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

_WORD_PATTERN = re.compile(r"[\w一-鿿]+", re.UNICODE)
_MAX_CLUSTER_ROUNDS = 30
_EMPTY_REASSIGN_ATTEMPTS = 5
_DEFAULT_N_CLUSTERS = 4


# ---------- vectorization ----------


def tokenize(text: str) -> list[str]:
    """Lowercased word tokens plus single CJK characters."""
    words = _WORD_PATTERN.findall(str(text or "").lower())
    tokens: list[str] = []
    for word in words:
        if len(word) == 1 and "一" <= word <= "鿿":
            tokens.append(word)
        elif len(word) > 1:
            tokens.append(word)
    return tokens


def _text_vector(text: str, vocab: dict[str, int]) -> list[float]:
    """TF vector over a shared vocabulary, with a 0.5 char-bigram prior."""
    tokens = tokenize(text)
    if not tokens:
        return [0.0] * len(vocab)
    counts = Counter(tokens)
    bigrams: Counter[str] = Counter()
    flat = "".join(tokens)
    for i in range(len(flat) - 1):
        bigrams[flat[i : i + 2]] += 1
    vector = [0.0] * len(vocab)
    for token, count in counts.items():
        if token in vocab:
            vector[vocab[token]] += count
    # bigram signal beyond the word vocabulary, normalised by length
    for gram, count in bigrams.items():
        if gram in vocab:
            vector[vocab[gram]] += count * 0.5
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def _build_vocab(texts: list[str], max_size: int = 256) -> dict[str, int]:
    """Shared vocabulary: most frequent tokens + char bigrams."""
    word_counts: Counter[str] = Counter()
    bigram_counts: Counter[str] = Counter()
    for text in texts:
        tokens = tokenize(text)
        word_counts.update(tokens)
        flat = "".join(tokens)
        for i in range(len(flat) - 1):
            bigram_counts[flat[i : i + 2]] += 1
    ordered = [t for t, _ in word_counts.most_common(max_size // 2)]
    ordered += [g for g, _ in bigram_counts.most_common(max_size - len(ordered))]
    # A bigram may equal a word token (e.g. "食品"); dedupe so the vocab
    # index space matches the vector length exactly.
    deduped: list[str] = []
    for token in ordered:
        if token not in deduped:
            deduped.append(token)
    return {token: i for i, token in enumerate(deduped)}


# ---------- cosine K-Means ----------


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot  # vectors are length-normalised


def _init_centroids(vectors: list[list[float]], k: int, rng: random.Random) -> list[list[float]]:
    """kmeans++ seeding: pick far-apart initial centroids."""
    centroids = [vectors[rng.randrange(len(vectors))]]
    for _ in range(1, k):
        # farthest members (lowest similarity) get the largest weight
        distances = [
            1.0 - min(_cosine(v, c) for c in centroids) for v in vectors
        ]
        weight_sum = sum(distances)
        if weight_sum <= 0:
            break
        pick = rng.random() * weight_sum
        cumulative = 0.0
        chosen = 0
        for i, distance in enumerate(distances):
            cumulative += distance
            if cumulative >= pick:
                chosen = i
                break
        centroids.append(vectors[chosen])
    return centroids


def kmeans(texts: list[str], k: int | None = None) -> list[int]:
    """Cluster texts by cosine similarity; returns cluster id per item.

    Deterministic (seeded RNG), kmeans++ init, empty-cluster reassignment.
    k defaults to min(len(texts), _DEFAULT_N_CLUSTERS) and degrades to 1
    when there are fewer than 2 texts.
    """
    count = len(texts)
    if count == 0:
        return []
    if count == 1:
        return [0]
    vocab = _build_vocab(texts)
    vectors = [_text_vector(text, vocab) for text in texts]
    k = max(1, min(k or _DEFAULT_N_CLUSTERS, count))
    if k == 1 or not vocab:
        return [0] * count

    rng = random.Random(42)
    centroids = _init_centroids(vectors, k, rng)
    if len(centroids) < k:  # too many identical texts for distinct seeds
        while len(centroids) < k:
            centroids.append(list(centroids[0]))
    assignments = [0] * count

    for _ in range(_MAX_CLUSTER_ROUNDS):
        new_assignments = [
            max(range(k), key=lambda c: _cosine(v, centroids[c])) for v in vectors
        ]
        if new_assignments == assignments:
            assignments = new_assignments
            break
        assignments = new_assignments
        groups: list[list[list[float]]] = [[] for _ in range(k)]
        for vector, cluster in zip(vectors, assignments, strict=True):
            groups[cluster].append(vector)
        for c in range(k):
            if groups[c]:
                centroids[c] = [
                    sum(v[i] for v in groups[c]) / len(groups[c])
                    for i in range(len(vectors[0]))
                ]
        # re-seed empty clusters with the farthest member of the largest one
        empty = [c for c in range(k) if not groups[c]]
        for _ in range(_EMPTY_REASSIGN_ATTEMPTS):
            if not empty:
                break
            largest = max(
                (c for c in range(k) if groups[c]),
                key=lambda c: len(groups[c]),
            )
            non_empty = [c for c in range(k) if groups[c]]
            candidates = [
                i for i in range(count) if assignments[i] == largest
            ]
            if not candidates:
                break
            far_member = min(
                candidates,
                key=lambda i: max(
                    _cosine(vectors[i], centroids[c2]) for c2 in non_empty
                ),
            )
            centroids[empty.pop()] = list(vectors[far_member])
    return assignments


# ---------- theme naming (TF-IDF) ----------


def theme_for(texts: list[str], top_k: int = 3) -> list[str]:
    """Most distinctive tokens of a cluster, TF-IDF weighted against all."""
    if not texts:
        return []
    cluster_tf: Counter[str] = Counter()
    document_counts: Counter[str] = Counter()
    for text in texts:
        tokens = set(tokenize(text))
        cluster_tf.update(tokens)
        document_counts.update(tokens)
    if not cluster_tf:
        return []
    count = len(texts)
    scored = sorted(
        cluster_tf,
        key=lambda token: (
            cluster_tf[token] * (math.log(count) - math.log(document_counts[token]) + 1.0),
            token,
        ),
        reverse=True,
    )
    return scored[:top_k]


# ---------- opinion groups ----------


def opinion_groups(
    posts: list[dict[str, Any]],
    texts: list[str],
) -> list[dict[str, Any]]:
    """Cluster posts into opinion groups with representative posts."""
    if not posts:
        return []
    assignments = kmeans(texts)
    groups: dict[int, list[int]] = defaultdict(list)
    for index, cluster in enumerate(assignments):
        groups[cluster].append(index)
    ordered = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)
    result: list[dict[str, Any]] = []
    for cluster, indices in ordered:
        group_texts = [texts[i] for i in indices]
        scores = [float(posts[i].get("score") or 0) for i in indices]
        result.append(
            {
                "id": cluster,
                "size": len(indices),
                "share": round(len(indices) / max(len(posts), 1) * 100, 1),
                "themes": theme_for(group_texts),
                "representative_post_id": str(posts[indices[0]].get("id") or ""),
                "avg_score": round(sum(scores) / max(len(scores), 1), 3),
            }
        )
    return result


# ---------- time series and trend breakpoints ----------


def _parse_post_datetime(post: dict[str, Any]) -> datetime | None:
    raw = post.get("published_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


def hourly_series(
    posts: list[dict[str, Any]],
    sentiments: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Per-hour buckets with post count and sentiment share."""
    if sentiments is None:
        sentiments = ["neutral"] * len(posts)
    buckets: dict[str, Counter[str]] = defaultdict(Counter)
    for post, sentiment in zip(posts, sentiments, strict=True):
        parsed = _parse_post_datetime(post)
        if parsed is None:
            continue
        key = parsed.strftime("%Y-%m-%dT%H:00")
        buckets[key][sentiment] += 1
        buckets[key]["__total"] += 1
    if not buckets:
        return []
    result: list[dict[str, Any]] = []
    for key in sorted(buckets):
        counts = buckets[key]
        total = counts["__total"]
        result.append(
            {
                "bucket": key,
                "count": total,
                "positive": round(counts.get("positive", 0) / total * 100, 1),
                "neutral": round(counts.get("neutral", 0) / total * 100, 1),
                "negative": round(counts.get("negative", 0) / total * 100, 1),
            }
        )
    return result


def trend_breakpoints(series: list[dict[str, Any]], window: int = 3) -> list[dict[str, Any]]:
    """Mark buckets where the following window's rate at least doubles the
    previous window's (post volume surge = potential burst point)."""
    if len(series) < window * 2 + 1:
        return []
    counts = [item["count"] for item in series]
    breakpoints: list[dict[str, Any]] = []
    for i in range(window, len(series) - window):
        before = sum(counts[i - window : i])
        after = sum(counts[i + 1 : i + 1 + window])
        if before <= 0:
            continue
        ratio = after / before
        if ratio >= 2.0:
            breakpoints.append(
                {
                    "bucket": series[i]["bucket"],
                    "before": before,
                    "after": after,
                    "surge_ratio": round(ratio, 2),
                }
            )
    return breakpoints


# ---------- influencer ranking ----------


def _engagement(post: dict[str, Any]) -> float:
    return float(
        post.get("like_count")
        or post.get("likes")
        or post.get("engagement")
        or 0
    ) + float(post.get("comment_count") or post.get("comments") or 0) + float(
        post.get("share_count") or post.get("reposts") or 0
    )


def influencer_ranking(posts: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """Rank authors by engagement volume with a log-scaled reach factor."""
    accounts: dict[str, dict[str, float]] = defaultdict(
        lambda: {"posts": 0.0, "engagement": 0.0, "followers": 0.0}
    )
    for post in posts:
        author = str(post.get("author") or "")
        if not author:
            continue
        accounts[author]["posts"] += 1
        accounts[author]["engagement"] += _engagement(post)
        accounts[author]["followers"] = max(
            accounts[author]["followers"],
            float(post.get("follower_count") or 0),
        )
    ranked = sorted(
        accounts.items(),
        key=lambda kv: (
            kv[1]["engagement"] * math.log1p(max(kv[1]["followers"], 1))
            + kv[1]["posts"],
        ),
        reverse=True,
    )
    return [
        {
            "author": author,
            "posts": int(stats["posts"]),
            "engagement": int(stats["engagement"]),
            "followers": int(stats["followers"]),
            "score": round(
                stats["engagement"] * math.log1p(max(stats["followers"], 1))
                + stats["posts"],
                2,
            ),
        }
        for author, stats in ranked[:limit]
    ]


# ---------- grounded statistical explanation ----------


def explain_opinion_statistics(
    result: dict[str, Any],
    posts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Turn computed opinion keys into a citable Chinese explanation.

    Numbers come only from ``result``; evidence IDs come only from
    representative posts / known post ids in ``posts``. No narrative is
    invented when the corresponding key is empty.
    """
    stats = result.get("statistics") or {}
    parts: list[str] = []
    evidence_ids: list[str] = []
    known_ids = {
        str(post.get("id")) for post in posts if post.get("id")
    }

    total = int(stats.get("total_posts") or 0)
    parts.append(f"当前样本共 {total} 条帖子")

    platforms = stats.get("platform_distribution") or {}
    if platforms:
        ranked_platforms = sorted(
            platforms.items(), key=lambda item: -int(item[1])
        )
        top_name, top_count = ranked_platforms[0]
        parts.append(f"其中 {top_name} {top_count} 条，占比最高")

    sentiment = stats.get("sentiment_distribution") or {}
    if sentiment:
        label, share = max(sentiment.items(), key=lambda item: float(item[1]))
        parts.append(f"情感以 {label} 为主（{share}%）")

    for cluster in result.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        themes = "、".join(str(item) for item in (cluster.get("themes") or [])[:3])
        theme_text = themes or "未命名主题"
        share = cluster.get("share", 0)
        rep = str(cluster.get("representative_post_id") or "")
        clause = f"观点群体「{theme_text}」占 {share}%"
        if rep and rep in known_ids:
            clause += f"，代表帖 {rep}"
            evidence_ids.append(rep)
        parts.append(clause)

    trends = result.get("trends") or []
    if trends:
        first = trends[0]
        parts.append(
            f"在 {first.get('bucket')} 出现音量突变"
            f"（倍率 {first.get('surge_ratio')}）"
        )

    influencers = result.get("influencers") or []
    if influencers:
        top = influencers[0]
        author = str(top.get("author") or "")
        if author:
            parts.append(f"互动量最高账号为 {author}")
            for post in posts:
                if str(post.get("author") or "") == author and post.get("id"):
                    evidence_ids.append(str(post["id"]))
                    break

    text = "；".join(parts) + "。" if parts else "当前样本不足以形成统计解释。"
    # Deduplicate while preserving order.
    unique_ids: list[str] = []
    for item in evidence_ids:
        if item not in unique_ids:
            unique_ids.append(item)
    return {
        "text": text,
        "evidence_ids": unique_ids,
        "source": "statistics",
    }
