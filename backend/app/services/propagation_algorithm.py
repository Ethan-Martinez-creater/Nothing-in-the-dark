"""Real propagation candidate-edge algorithm (v1.0.0).

Rules:
* ``observed`` edges require an explicit platform relation in the raw
  payload (reply / quote / retweet / quoted URL). Posting order alone can
  NEVER produce an observed edge.
* ``inferred`` edges combine time decay, text similarity (BGE-M3 vectors
  when available) and entity overlap; every edge carries feature_scores,
  an algorithm version and evidence ids.
* Nodes that do not exist are never referenced: edges are only built
  between posts present in the input sample.

The core functions are pure and dependency-free so they can be unit
tested; ``reconstruct_propagation`` is the async wrapper that may call
the embedding worker for text similarity.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.infrastructure.embeddings import EmbeddingWorkerClient

ALGORITHM_VERSION = "1.1.0"

# Hours between two posts after which an inferred edge is not considered.
_MAX_TIME_WINDOW_HOURS = 168.0
# Confidence below which inferred edges are dropped.
_MIN_INFERRED_CONFIDENCE = 0.35
# Cosine similarity threshold for a text-similarity edge.
_TEXT_SIMILARITY_THRESHOLD = 0.6
# Maximum nodes a batch computes pairwise similarity for (O(n^2) guard).
_MAX_SIMILARITY_NODES = 200

# Explicit relation keys looked up in the post dict AND its raw payload.
_OBSERVED_RELATION_KEYS = ("reply_to_id", "quote_id", "retweet_of_id")

ENTITY_PATTERNS = (
    re.compile(r"20\d{2}[-/年]\d{1,2}(?:[-/月]\d{1,2})?"),  # dates
    re.compile(r"\d+(?:\.\d+)?%"),  # percentages
    re.compile(r"¥\s?\d+(?:\.\d+)?|\d+\s?元"),  # amounts
    re.compile(r"\d+(?:\.\d+)?万|\d+(?:\.\d+)?亿"),  # scales
    re.compile(r"\d{3,}"),  # long number sequences (IDs, counts)
)


@dataclass(frozen=True, slots=True)
class EdgeCandidate:
    """One candidate propagation edge with its feature scores."""

    source_post_id: str
    target_post_id: str
    relation: str  # "observed" | "inferred"
    confidence: float
    feature_scores: dict[str, float]
    reasons: list[str]
    evidence_ids: list[str] = field(default_factory=list)


def extract_entities(text: str) -> set[str]:
    """Extract entity-like tokens (dates, amounts, scales, numbers)."""
    entities: set[str] = set()
    for pattern in ENTITY_PATTERNS:
        entities.update(match.group(0) for match in pattern.finditer(text))
    return entities


def _entity_overlap(a: str, b: str) -> float:
    entities_a = extract_entities(a)
    entities_b = extract_entities(b)
    if not entities_a or not entities_b:
        return 0.0
    shared = len(entities_a & entities_b)
    return shared / max(len(entities_a), len(entities_b), 1)


def _time_decay(a: datetime | None, b: datetime | None) -> float:
    if a is None or b is None:
        return 0.0
    hours = abs((a - b).total_seconds()) / 3600.0
    if hours > _MAX_TIME_WINDOW_HOURS:
        return 0.0
    return 1.0 / (1.0 + hours / 24.0)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _post_value(post: dict[str, Any], key: str) -> Any:
    """Look a field up in the post dict, then inside its raw payload."""
    if key in post:
        return post.get(key)
    raw = post.get("raw") or post.get("raw_payload")
    if isinstance(raw, dict) and key in raw:
        return raw.get(key)
    return None


def _post_datetime(post: dict[str, Any]) -> datetime | None:
    value = post.get("published_at")
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.isdigit():
        timestamp = float(text)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


def extract_observed_edges(
    posts: list[dict[str, Any]],
) -> list[EdgeCandidate]:
    """Explicit platform relations: reply / quote / retweet / URL links.

    An edge is only emitted when BOTH endpoints exist in the sample; the
    target is the replying/quoting/retweeting post, the source the post it
    refers to (edge direction source -> target).
    """
    ids = {str(post.get("id")) for post in posts if post.get("id")}
    edges: list[EdgeCandidate] = []
    seen: set[tuple[str, str]] = set()
    for post in posts:
        post_id = str(post.get("id"))
        if not post_id:
            continue
        for key in _OBSERVED_RELATION_KEYS:
            target_id = _post_value(post, key)
            if not target_id:
                continue
            target_id = str(target_id)
            if target_id not in ids:
                continue
            if (target_id, post_id) in seen:
                continue
            seen.add((target_id, post_id))
            overlap = _entity_overlap(
                str(post.get("content") or ""),
                str(next(
                    (str(p.get("content") or "")
                     for p in posts if str(p.get("id")) == target_id),
                    "",
                )),
            )
            confidence = round(min(0.95, 0.85 + 0.1 * overlap), 3)
            edges.append(
                EdgeCandidate(
                    source_post_id=target_id,
                    target_post_id=post_id,
                    relation="observed",
                    confidence=confidence,
                    feature_scores={
                        "explicit_relation": 1.0,
                        "entity_overlap": round(overlap, 3),
                        "time_decay": round(
                            _time_decay(
                                _post_datetime(post),
                                _post_datetime(
                                    next(
                                        (p for p in posts
                                         if str(p.get("id")) == target_id),
                                        {},
                                    )
                                ),
                            ),
                            3,
                        ),
                    },
                    reasons=[f"平台显式{key}关系"],
                    evidence_ids=[target_id, post_id],
                )
            )
    return edges


def compute_inferred_edges(
    posts: list[dict[str, Any]],
    *,
    embeddings: list[list[float]] | None = None,
) -> list[EdgeCandidate]:
    """Candidate edges from time decay + text similarity + entity overlap.

    ``embeddings`` must align with ``posts`` (one vector per post, already
    normalized); when missing, text similarity is scored 0 and only time +
    entity signals remain, so confidence rarely passes the threshold.
    """
    nodes = [post for post in posts if post.get("id")]
    if len(nodes) < 2:
        return []
    embeddings = embeddings or [None] * len(nodes)
    if len(embeddings) != len(nodes):
        embeddings = [None] * len(nodes)

    edges: list[EdgeCandidate] = []
    seen: set[tuple[str, str]] = set()
    for index, source in enumerate(nodes):
        for other_index in range(index + 1, len(nodes)):
            target = nodes[other_index]
            source_id = str(source.get("id"))
            target_id = str(target.get("id"))
            pair = tuple(sorted((source_id, target_id)))
            if pair in seen:
                continue
            seen.add(pair)

            decay = _time_decay(
                _post_datetime(source),
                _post_datetime(target),
            )
            if decay <= 0.0:
                continue
            overlap = _entity_overlap(
                str(source.get("content") or ""),
                str(target.get("content") or ""),
            )
            similarity = (
                _cosine_similarity(embeddings[index], embeddings[other_index])
                if embeddings[index] is not None
                and embeddings[other_index] is not None
                else 0.0
            )
            if (
                similarity < _TEXT_SIMILARITY_THRESHOLD
                and overlap <= 0.0
            ):
                continue

            confidence = 0.25 * decay + 0.45 * similarity + 0.3 * overlap
            if confidence < _MIN_INFERRED_CONFIDENCE:
                continue
            # Temporal direction: earlier post is the source.
            earlier, later = sorted(
                (source, target),
                key=lambda post: _post_datetime(post) or datetime.max.replace(
                    tzinfo=UTC
                ),
            )
            reasons = [
                "同案例候选传播（未证实）",
                "文本相似度" if similarity >= _TEXT_SIMILARITY_THRESHOLD
                else "实体重合",
            ]
            edges.append(
                EdgeCandidate(
                    source_post_id=str(earlier.get("id")),
                    target_post_id=str(later.get("id")),
                    relation="inferred",
                    confidence=round(min(confidence, 0.85), 3),
                    feature_scores={
                        "time_decay": round(decay, 3),
                        "text_similarity": round(similarity, 3),
                        "entity_overlap": round(overlap, 3),
                    },
                    reasons=reasons,
                    evidence_ids=[str(earlier.get("id")), str(later.get("id"))],
                )
            )
    return edges


def compute_origin_candidates(
    nodes: list[dict[str, Any]],
    edges: list[EdgeCandidate],
) -> list[dict[str, Any]]:
    """Earliest posts plus high out-degree hubs as source candidates."""
    if not nodes:
        return []
    out_degree: dict[str, int] = {}
    for edge in edges:
        out_degree[edge.source_post_id] = out_degree.get(edge.source_post_id, 0) + 1
    ordered = sorted(
        nodes,
        key=lambda post: _post_datetime(post) or datetime.max.replace(tzinfo=UTC),
    )
    candidates: list[dict[str, Any]] = []
    earliest = ordered[0]
    candidates.append(
        {
            "node_id": str(earliest.get("id")),
            "confidence": round(
                0.5 + min(0.2, 0.05 * out_degree.get(str(earliest.get("id")), 0)),
                3,
            ),
            "reason": "当前样本中发布时间最早（候选，需进一步核实）。",
        }
    )
    hubs = [
        post
        for post in ordered[1:]
        if out_degree.get(str(post.get("id")), 0) >= 2
    ]
    for hub in hubs[:3]:
        candidates.append(
            {
                "node_id": str(hub.get("id")),
                "confidence": round(
                    min(0.8, 0.4 + 0.1 * out_degree.get(str(hub.get("id")), 0)),
                    3,
                ),
                "reason": "多条候选边以此为源（桥接/爆发节点候选）。",
            }
        )
    return candidates


def build_propagation_graph(
    posts: list[dict[str, Any]],
    *,
    embeddings: list[list[float]] | None = None,
) -> dict[str, Any]:
    """Assemble nodes, edges and origin candidates for one sample."""
    ordered_posts = sorted(
        posts,
        key=lambda post: _post_datetime(post) or datetime.min.replace(tzinfo=UTC),
    )
    nodes = [
        {
            "id": str(post["id"]),
            "label": str(post.get("author") or ""),
            "platform": str(post.get("platform") or ""),
            "published_at": str(post.get("published_at") or ""),
            "content": str(post.get("content") or ""),
            "engagement": int(post.get("engagement") or 0),
        }
        for post in ordered_posts
    ]
    observed = extract_observed_edges(ordered_posts)
    inferred = compute_inferred_edges(
        ordered_posts,
        embeddings=embeddings,
    )
    edges = [
        {
            "id": f"edge-observed-{index + 1}",
            "source": edge.source_post_id,
            "target": edge.target_post_id,
            "relation": edge.relation,
            "confidence": edge.confidence,
            "feature_scores": edge.feature_scores,
            "reasons": edge.reasons,
            "evidence_ids": edge.evidence_ids,
            "algorithm_version": ALGORITHM_VERSION,
        }
        for index, edge in enumerate(observed)
    ]
    edges.extend(
        {
            "id": f"edge-inferred-{index + 1}",
            "source": edge.source_post_id,
            "target": edge.target_post_id,
            "relation": edge.relation,
            "confidence": edge.confidence,
            "feature_scores": edge.feature_scores,
            "reasons": edge.reasons,
            "evidence_ids": edge.evidence_ids,
            "algorithm_version": ALGORITHM_VERSION,
        }
        for index, edge in enumerate(inferred)
    )
    candidate_edges = observed + inferred
    return {
        "nodes": nodes,
        "edges": edges,
        "origin_candidates": compute_origin_candidates(ordered_posts, candidate_edges),
        # M7c: media reuse fingerprints, cross-platform account mapping,
        # rule-based edge critique and node role labels.
        "media_fingerprints": media_fingerprints(ordered_posts),
        "account_groups": map_cross_platform_accounts(ordered_posts),
        "critique": criticize_edges(ordered_posts, candidate_edges),
        "node_roles": compute_node_roles(ordered_posts, candidate_edges),
        "algorithm_version": ALGORITHM_VERSION,
        "limitations": [
            "传播图只覆盖当前已采集样本。",
            "inferred 边表示概率候选关系，不代表已证明的转发关系。",
            "仅发布时间相邻不能自动成为 observed 边。",
        ],
        "is_demo": any(bool(post.get("is_demo")) for post in posts),
    }


async def reconstruct_propagation(
    posts: list[dict[str, Any]],
    *,
    embedding_client: EmbeddingWorkerClient | None = None,
    llm: Any = None,
) -> dict[str, Any]:
    """Async wrapper: adds BGE-M3 text similarity when the worker is up."""
    embeddings: list[list[float]] | None = None
    bounded = posts[: _MAX_SIMILARITY_NODES]
    if embedding_client is not None and embedding_client.configured:
        try:
            embeddings = await embedding_client.embed(
                [str(post.get("content") or "") for post in bounded]
            )
        except Exception:
            embeddings = None
    graph = build_propagation_graph(bounded, embeddings=embeddings)
    graph["critique"] = await criticize_edges_with_llm(
        llm, bounded, graph.get("critique") or {"kept": [], "rejected": [], "notes": []}
    )
    return graph


# ---------------------------------------------------------------------------
# M7c extensions: media fingerprints, cross-platform account mapping,
# rule-based edge critic and node role labelling.
# ---------------------------------------------------------------------------

# Query params stripped from media URLs: tracking / signature noise.
_TRACKING_PARAMS = (
    "token",
    "sign",
    "signature",
    "access_token",
    "expires",
    "expire",
    "timestamp",
    "spm",
    "from",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "wx_fmt",
)

# Post fields that may carry media attachments (either as a string URL or a
# list of URLs / dicts with a "url" key).
_MEDIA_URL_KEYS = (
    "image_url",
    "video_url",
    "images",
    "image_urls",
    "media_urls",
    "cover_url",
)


def normalize_media_url(url: str) -> str:
    """Deterministic canonical form of a media URL.

    Scheme and host are lowercased, the default port dropped, tracking
    query params removed and any fragment stripped. Two URLs pointing at
    the same file with different signed tokens therefore collapse.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    match = re.match(
        r"^(https?://)?([^/?#]+)([^?#]*)(?:\?([^#]*))?(?:#.*)?$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return text
    scheme = (match.group(1) or "https://").lower()
    host = match.group(2).lower()
    path = (match.group(3) or "").lower()
    query = match.group(4)
    if not query:
        return f"{scheme}{host}{path}".rstrip("/")
    kept = [
        part for part in query.split("&")
        if part.split("=", 1)[0].lower() not in _TRACKING_PARAMS
    ]
    suffix = f"?{'&'.join(kept)}" if kept else ""
    return f"{scheme}{host}{path}{suffix}".rstrip("/")


def url_fingerprint(url: str) -> str:
    """Short deterministic content-address-ish id of a normalized URL."""
    return hashlib.sha256(normalize_media_url(url).encode("utf-8")).hexdigest()[:16]


def _extract_media_urls(post: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in _MEDIA_URL_KEYS:
        value = _post_value(post, key)
        if isinstance(value, str):
            urls.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    urls.append(item)
                elif isinstance(item, dict) and item.get("url"):
                    urls.append(str(item["url"]))
    return urls


def media_fingerprints(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicated normalized URLs with fingerprints, per source post.

    The same media shared on different platforms keeps one fingerprint,
    which the account mapping and node roles use as a reuse signal.
    """
    seen: dict[str, dict[str, Any]] = {}
    for post in posts:
        post_id = str(post.get("id") or "")
        for url in _extract_media_urls(post):
            normalized = normalize_media_url(url)
            if not normalized:
                continue
            entry = seen.setdefault(
                normalized,
                {
                    "url": normalized,
                    "fingerprint": url_fingerprint(url),
                    "post_ids": [],
                    "platforms": [],
                },
            )
            if post_id and post_id not in entry["post_ids"]:
                entry["post_ids"].append(post_id)
            platform = str(post.get("platform") or "")
            if platform and platform not in entry["platforms"]:
                entry["platforms"].append(platform)
    return sorted(seen.values(), key=lambda item: len(item["post_ids"]), reverse=True)


# ---------- cross-platform account mapping ----------

# Common suffixes/prefixes stripped before comparing account names.
_ACCOUNT_NOISE_PATTERNS = (
    re.compile(r"[_\-·\s]"),
    re.compile(r"^(?:weibo|bilibili|抖音|微博|快手|官方|official|verified)[_\-:：]?"),
    re.compile(r"(?:的微博|的账号|official|verified|官微|weibo|bilibili|抖音|快手)[_\-:：]?$"),
)


def normalize_account_name(name: str) -> str:
    """Fold case, full-width chars and platform noise off a display name."""
    text = unicodedata.normalize("NFKC", str(name or "")).lower().strip()
    for pattern in _ACCOUNT_NOISE_PATTERNS:
        text = pattern.sub("", text)
    return text


def _levenshtein(a: str, b: str) -> int:
    """Edit distance (pure stdlib, O(min(a,b)) row cache)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) > len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (char_a != char_b),
                )
            )
        previous = current
    return previous[-1]


# Max edit distance for two names to be treated as the same account.
_ACCOUNT_NAME_THRESHOLD = 2


def map_cross_platform_accounts(
    posts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group posts by account identity across platforms.

    Exact normalized-name matches merge first; remaining names within the
    edit-distance threshold of the group representative join the group.
    """
    accounts: dict[str, dict[str, Any]] = {}
    for post in posts:
        author = str(post.get("author") or "")
        if not author:
            continue
        normalized = normalize_account_name(author)
        if not normalized:
            continue
        entry = accounts.setdefault(
            normalized,
            {
                "normalized_name": normalized,
                "display_names": [],
                "platforms": [],
                "post_ids": [],
            },
        )
        if author not in entry["display_names"]:
            entry["display_names"].append(author)
        platform = str(post.get("platform") or "")
        if platform and platform not in entry["platforms"]:
            entry["platforms"].append(platform)
        if str(post.get("id")) not in entry["post_ids"]:
            entry["post_ids"].append(str(post.get("id")))

    groups: list[dict[str, Any]] = []
    pending = list(accounts.values())
    while pending:
        representative = pending.pop(0)
        members = [representative]
        remaining: list[dict[str, Any]] = []
        for other in pending:
            if (
                _levenshtein(
                    representative["normalized_name"],
                    other["normalized_name"],
                )
                <= _ACCOUNT_NAME_THRESHOLD
            ):
                members.append(other)
            else:
                remaining.append(other)
        pending = remaining
        merged = {
            "identity": representative["normalized_name"],
            "display_names": sorted(
                {name for member in members for name in member["display_names"]}
            ),
            "platforms": sorted(
                {platform for member in members for platform in member["platforms"]}
            ),
            "post_count": sum(len(member["post_ids"]) for member in members),
        }
        merged["cross_platform"] = len(merged["platforms"]) > 1
        groups.append(merged)
    groups.sort(key=lambda group: -group["post_count"])
    return groups


# ---------- rule-based edge critic ----------


def criticize_edges(
    posts: list[dict[str, Any]],
    edges: list[EdgeCandidate],
) -> dict[str, Any]:
    """Rule-based review of candidate edges (LLM-free fallback).

    Rejects edges whose target post predates the source (clock skew in raw
    data) and demotes low-evidence inferred edges that share neither text
    similarity nor entity overlap nor a media fingerprint.
    """
    posts_by_id = {str(post.get("id")): post for post in posts}
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    notes: list[str] = []
    for edge in edges:
        source_post = posts_by_id.get(edge.source_post_id)
        target_post = posts_by_id.get(edge.target_post_id)
        source_time = _post_datetime(source_post or {})
        target_time = _post_datetime(target_post or {})
        if (
            source_time is not None
            and target_time is not None
            and target_time < source_time
            and edge.relation == "observed"
        ):
            rejected.append(
                {
                    "id": edge.source_post_id + "->" + edge.target_post_id,
                    "reason": "observed 边目标帖早于源帖，原始数据时间戳异常。",
                }
            )
            notes.append("拒绝了 1 条时间倒流的 observed 边。")
            continue
        feature = edge.feature_scores
        similarity = feature.get("text_similarity", 0.0)
        overlap = feature.get("entity_overlap", 0.0)
        fingerprint = (
            _media_reuse_between(
                source_post or {}, target_post or {}, posts
            )
        )
        if (
            edge.relation == "inferred"
            and similarity < _TEXT_SIMILARITY_THRESHOLD
            and overlap <= 0.0
            and not fingerprint
        ):
            rejected.append(
                {
                    "id": edge.source_post_id + "->" + edge.target_post_id,
                    "reason": "inferred 边无文本相似度、实体重合或媒体复用证据。",
                }
            )
            notes.append("拒绝了 1 条低证据 inferred 边。")
            continue
        reviewed = edge
        if fingerprint:
            reviewed = EdgeCandidate(
                source_post_id=edge.source_post_id,
                target_post_id=edge.target_post_id,
                relation=edge.relation,
                confidence=round(min(edge.confidence + 0.1, 0.95), 3),
                feature_scores={**edge.feature_scores, "media_reuse": 1.0},
                reasons=edge.reasons + ["两端共享同一媒体指纹"],
                evidence_ids=edge.evidence_ids,
            )
            notes.append("1 条边因媒体复用获得置信度加成。")
        kept.append(
            {
                "id": reviewed.source_post_id + "->" + reviewed.target_post_id,
                "source": reviewed.source_post_id,
                "target": reviewed.target_post_id,
                "relation": reviewed.relation,
                "confidence": reviewed.confidence,
                "reasons": reviewed.reasons,
            }
        )
    return {"kept": kept, "rejected": rejected, "notes": notes}


def _media_reuse_between(
    source: dict[str, Any],
    target: dict[str, Any],
    posts: list[dict[str, Any]],
) -> bool:
    source_fps = {
        url_fingerprint(url) for url in _extract_media_urls(source)
    }
    target_fps = {url_fingerprint(url) for url in _extract_media_urls(target)}
    if source_fps & target_fps:
        return True
    # Pixel-hash / keyframe reuse (P0-1.1b/c). Imported lazily so this
    # module stays importable without media_features in older tests.
    try:
        from app.services.media_features import (
            keyframe_fingerprints,
            phashes_from_post,
            similar_phash,
        )
    except Exception:
        return False
    source_hashes = phashes_from_post(source)
    target_hashes = phashes_from_post(target)
    for left in source_hashes:
        for right in target_hashes:
            if similar_phash(left, right):
                return True
    if keyframe_fingerprints(source) & keyframe_fingerprints(target):
        return True
    return False


_AMBIGUOUS_CONFIDENCE = 0.6


async def criticize_edges_with_llm(
    llm: Any,
    posts: list[dict[str, Any]],
    critique: dict[str, Any],
) -> dict[str, Any]:
    """Ask the model to review ambiguous inferred edges.

    When the gateway is missing or not configured the rule-based critique
    is returned unchanged and ``llm_review.available`` is False. A
    ``reject`` verdict moves the edge from ``kept`` to ``rejected``.
    """
    if llm is None or not getattr(llm, "configured", False):
        critique["llm_review"] = {"available": False, "reviews": []}
        return critique

    from app.harness.structured_output import repair_json_content
    from app.infrastructure.llm import LLMMessage, ModelRoute

    ambiguous = [
        edge
        for edge in (critique.get("kept") or [])
        if edge.get("relation") == "inferred"
        and float(edge.get("confidence") or 0) < _AMBIGUOUS_CONFIDENCE
    ]
    if not ambiguous:
        critique["llm_review"] = {
            "available": True,
            "reviews": [],
            "skipped": "none_ambiguous",
        }
        return critique

    payload = {
        "posts": [
            {
                "id": post.get("id"),
                "content": str(post.get("content") or "")[:160],
                "published_at": str(post.get("published_at") or ""),
            }
            for post in posts
        ],
        "edges": ambiguous,
    }
    prompt = (
        "你是传播边审核员。只依据给定帖子与边特征判断含糊 inferred 边是否成立。"
        "只输出 JSON：{\"reviews\": [{\"source\": \"...\", \"target\": \"...\", "
        "\"verdict\": \"keep|reject\", \"reason\": \"...\"}]}\n"
        f"{payload}"
    )
    try:
        response = await llm.complete(
            messages=[
                LLMMessage(role="system", content="只输出 JSON，不虚构节点。"),
                LLMMessage(role="user", content=prompt),
            ],
            tools=[],
            route=ModelRoute.FAST,
        )
        content = response.message.content if response and response.message else ""
        parsed = repair_json_content(content) if content else {}
        reviews = list((parsed or {}).get("reviews") or [])
    except Exception:
        critique["llm_review"] = {"available": False, "reviews": [], "error": "llm_failed"}
        return critique

    reject_keys = {
        f"{item.get('source')}->{item.get('target')}"
        for item in reviews
        if str(item.get("verdict") or "").lower() == "reject"
    }
    kept: list[dict[str, Any]] = []
    rejected = list(critique.get("rejected") or [])
    notes = list(critique.get("notes") or [])
    for edge in critique.get("kept") or []:
        key = f"{edge.get('source')}->{edge.get('target')}"
        if key in reject_keys:
            rejected.append(
                {
                    "id": key,
                    "reason": next(
                        (
                            str(item.get("reason") or "LLM 拒绝")
                            for item in reviews
                            if f"{item.get('source')}->{item.get('target')}" == key
                        ),
                        "LLM 拒绝",
                    ),
                }
            )
            notes.append("LLM Critic 拒绝了 1 条含糊 inferred 边。")
            continue
        kept.append(edge)
    critique["kept"] = kept
    critique["rejected"] = rejected
    critique["notes"] = notes
    critique["llm_review"] = {"available": True, "reviews": reviews}
    return critique


# ---------- node role labelling ----------


def compute_node_roles(
    posts: list[dict[str, Any]],
    edges: list[EdgeCandidate],
    burst_window_hours: float = 24.0,
) -> list[dict[str, Any]]:
    """Label nodes as source / bridge / burst / spreader.

    * ``source`` — origin candidates from ``compute_origin_candidates``.
    * ``bridge`` — has both in- and out-degree (relays content on).
    * ``burst`` — emits many edges inside a short window (possible
      coordinated burst).
    * ``spreader`` — plain high-out-degree node otherwise.
    """
    out_degree: dict[str, int] = {}
    in_degree: dict[str, int] = {}
    first_edge_time: dict[str, datetime | None] = {}
    last_edge_time: dict[str, datetime | None] = {}
    for edge in edges:
        out_degree[edge.source_post_id] = out_degree.get(edge.source_post_id, 0) + 1
        in_degree[edge.target_post_id] = in_degree.get(edge.target_post_id, 0) + 1
        edge_time = _edge_time(posts, edge)
        if edge_time is not None:
            first_edge_time.setdefault(edge.source_post_id, edge_time)
            last_edge_time[edge.source_post_id] = edge_time
    origins = {
        candidate["node_id"]
        for candidate in compute_origin_candidates(posts, edges)
    }
    roles: list[dict[str, Any]] = []
    for post in posts:
        post_id = str(post.get("id"))
        if not post_id:
            continue
        out_d = out_degree.get(post_id, 0)
        in_d = in_degree.get(post_id, 0)
        first = first_edge_time.get(post_id)
        last = last_edge_time.get(post_id)
        if post_id in origins:
            role = "source"
        elif in_d > 0 and out_d > 0:
            role = "bridge"
        elif (
            out_d >= 2
            and first is not None
            and last is not None
            and (last - first).total_seconds() / 3600.0 <= burst_window_hours
        ):
            role = "burst"
        else:
            role = "spreader"
        roles.append(
            {
                "post_id": post_id,
                "role": role,
                "score": round(0.5 + 0.1 * out_d + 0.05 * in_d, 3),
                "out_degree": out_d,
                "in_degree": in_d,
            }
        )
    return roles


def _edge_time(
    posts: list[dict[str, Any]],
    edge: EdgeCandidate,
) -> datetime | None:
    for post in posts:
        if str(post.get("id")) == edge.source_post_id:
            return _post_datetime(post)
    return None
