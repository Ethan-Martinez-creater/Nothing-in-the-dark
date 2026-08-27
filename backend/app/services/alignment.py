"""Cross-platform alignment domain logic (06).

跨平台实体、内容与叙事对齐的确定性核心：规范化（NFKC/全半角/URL）、
n-gram 与 MinHash 文本相似、账号/内容候选评分、冲突降分与决策阈值、
无向候选键。原始平台对象永不合并；自动结果只到 probable，确定性标识
（真实 SHA-256 相同）才可直接 confirmed。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

# 决策状态与关系类型
DECISIONS = ("pending", "confirmed", "probable", "possible", "rejected")
RELATION_TYPES = ("same_as", "derived_from", "mentions", "narrative_member")

# 阈值（可配置）。自动 probable 阈值较高，达不到只进人工候选。
PROBABLE_THRESHOLD = 0.95
POSSIBLE_THRESHOLD = 0.70

# 账号评分权重
_ACCOUNT_WEIGHTS = {
    "name_similarity": 0.4,
    "avatar_phash_match": 0.4,
    "verified_consistent": 0.2,
}

# 内容评分权重
_CONTENT_WEIGHTS = {
    "sha256_match": 1.0,
    "phash_match": 0.5,
    "text_similarity": 0.5,
}


# ---- 规范化 ---------------------------------------------------------------

_FULLWIDTH_RE = re.compile(r"[\uFF01-\uFF5E]")
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def _halfwidth(char: str) -> str:
    code = ord(char)
    if 0xFF01 <= code <= 0xFF5E:
        return chr(code - 0xFEE0)
    return char


def normalize_name(name: str) -> str:
    """NFKC + 全角转半角 + 小写 + 空白折叠。显示名规范化是弱信号。"""
    normalized = unicodedata.normalize("NFKC", str(name or ""))
    normalized = "".join(_halfwidth(c) for c in normalized)
    return _WHITESPACE_RE.sub("", normalized).lower()


def normalize_text(text: str) -> str:
    """规范化文本用于相似度：NFKC + 小写 + 去标点空白。"""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = "".join(_halfwidth(c) for c in normalized)
    normalized = normalized.lower()
    return _PUNCT_RE.sub("", normalized)


_TRACKING_PARAMS = {
    "from",
    "ref",
    "spm",
    "utm_source",
    "utm_medium",
    "utm_campaign",
}


def normalize_url(url: str) -> str:
    """规范化 URL：scheme/host 小写，path 保留大小写，仅移除白名单跟踪参数。

    不再小写整个 path、也不删除全部 query，避免合并区分大小写或
    业务参数不同的资源。
    """
    parsed = str(url or "").strip()
    if not parsed:
        return ""
    if "://" not in parsed:
        return parsed
    scheme, rest = parsed.split("://", 1)
    path = rest.split("#", 1)[0]
    if "?" in path:
        base, query = path.split("?", 1)
        kept = [
            q
            for q in query.split("&")
            if q.split("=", 1)[0].lower() not in _TRACKING_PARAMS
        ]
        path = base + ("?" + "&".join(kept) if kept else "")
    if "/" in path:
        host, tail = path.split("/", 1)
        path = host.lower() + "/" + tail
    else:
        path = path.lower()
    return f"{scheme.lower()}://{path.rstrip('/')}"


# ---- 相似度 ---------------------------------------------------------------


def ngram_set(text: str, n: int = 3) -> set[str]:
    normalized = normalize_text(text)
    if len(normalized) < n:
        return {normalized} if normalized else set()
    return {normalized[i:i + n] for i in range(len(normalized) - n + 1)}


def jaccard_similarity(left: str, right: str, n: int = 3) -> float:
    a = ngram_set(left, n)
    b = ngram_set(right, n)
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _fnv1a(value: str, seed: int) -> int:
    h = 0x811C9DC5 ^ seed
    for byte in value.encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def minhash_signature(text: str, k: int = 128, n: int = 3) -> list[int]:
    """MinHash 签名（长文本近重复候选），无外部依赖。"""
    shingles = ngram_set(text, n)
    if not shingles:
        return [0] * k
    signature: list[int] = []
    for seed in range(k):
        minimum = 0xFFFFFFFF
        for shingle in shingles:
            h = _fnv1a(shingle, seed)
            if h < minimum:
                minimum = h
        signature.append(minimum)
    return signature


def minhash_jaccard(left: list[int], right: list[int]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    matches = sum(1 for a, b in zip(left, right, strict=False) if a == b)
    return matches / len(left)


# ---- 账号对齐 -------------------------------------------------------------


def account_alignment(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """账号候选评分，返回特征分解 + 综合分。显示名相同只是弱信号。"""
    features: dict[str, Any] = {}
    name_a = normalize_name(str(left.get("name", "")))
    name_b = normalize_name(str(right.get("name", "")))
    features["name_similarity"] = (
        1.0 if (name_a and name_a == name_b) else jaccard_similarity(name_a, name_b, 2)
    )

    phash_a = left.get("phash") or left.get("avatar_phash")
    phash_b = right.get("phash") or right.get("avatar_phash")
    if phash_a and phash_b:
        features["avatar_phash_match"] = 1.0 if phash_a == phash_b else 0.0
    else:
        features["avatar_phash_match"] = 0.0

    verified_a = left.get("verified")
    verified_b = right.get("verified")
    if verified_a is None or verified_b is None:
        # 认证状态未知：missing，不计入权重（不能当正分）。
        features["verified_consistent"] = None
    elif verified_a and verified_b:
        features["verified_consistent"] = 1.0
    elif not verified_a and not verified_b:
        # 两个未认证账号不构成"认证一致"的强证据。
        features["verified_consistent"] = None
    else:
        # 认证主体不一致：冲突降分。
        features["verified_consistent"] = 0.0

    score = _weighted_score(features, _ACCOUNT_WEIGHTS)
    return {"features": features, "score": score}


# ---- 内容对齐 -------------------------------------------------------------


def content_alignment(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """内容候选评分：真实 SHA-256 / pHash / 文本相似。"""
    features: dict[str, Any] = {}
    sha_a = left.get("sha256") or left.get("actual_sha256")
    sha_b = right.get("sha256") or right.get("actual_sha256")
    if sha_a and sha_b:
        features["sha256_match"] = 1.0 if sha_a == sha_b else 0.0
    else:
        features["sha256_match"] = 0.0

    phash_a = left.get("phash")
    phash_b = right.get("phash")
    if phash_a and phash_b:
        try:
            distance = _hamming_hex(phash_a, phash_b)
            features["phash_match"] = max(0.0, 1.0 - distance / 64.0)
        except ValueError:
            features["phash_match"] = 0.0
    else:
        features["phash_match"] = 0.0

    features["text_similarity"] = jaccard_similarity(
        str(left.get("content", "")), str(right.get("content", ""))
    )

    score = _weighted_score(features, _CONTENT_WEIGHTS)
    return {"features": features, "score": score}


def _hamming_hex(left: str, right: str) -> int:
    if len(left) != len(right):
        raise ValueError("hash length mismatch")
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def _weighted_score(features: dict[str, Any], weights: dict[str, float]) -> float:
    total = 0.0
    weight_sum = 0.0
    for key, weight in weights.items():
        value = features.get(key)
        if value is None:
            continue
        total += float(value) * weight
        weight_sum += weight
    if weight_sum == 0:
        return 0.0
    return total / weight_sum


# ---- 决策与无向键 ---------------------------------------------------------


def decide_relation(
    *,
    relation_type: str,
    score: float,
    features: dict[str, Any],
) -> tuple[str, float]:
    """把综合分映射到决策状态。

    确定性标识（真实 SHA-256 相同）直接 confirmed；其余只到 probable，
    达不到阈值保持 pending 进入人工候选，不得自动确认为同一主体。
    """
    if relation_type in ("same_as", "derived_from"):
        if features.get("sha256_match") == 1.0:
            return "confirmed", score
    if score >= PROBABLE_THRESHOLD:
        return "probable", score
    if score >= POSSIBLE_THRESHOLD:
        return "possible", score
    return "pending", score


def undirected_key(
    left_type: str,
    left_id: str,
    right_type: str,
    right_id: str,
) -> tuple[str, str]:
    """规范化无向键：排序后返回 (left_key, right_key)，禁止 A-B 与 B-A 重复。"""
    left_key = f"{left_type}:{left_id}"
    right_key = f"{right_type}:{right_id}"
    if left_key <= right_key:
        return left_key, right_key
    return right_key, left_key


def stable_fingerprint(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def evaluate_alignment_pairs(
    gold_pairs: set[tuple[str, str]],
    predicted_pairs: set[tuple[str, str]],
) -> dict[str, Any]:
    """候选召回与精排 P/R/F1（无向对评测）。

    gold_pairs 是真实同源对，predicted_pairs 是算法输出的 probable/confirmed
    对（规范化无向）。返回 precision/recall/f1 与计数。
    """
    gold = {tuple(sorted(p)) for p in gold_pairs}
    pred = {tuple(sorted(p)) for p in predicted_pairs}
    tp = len(gold & pred)
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(gold) if gold else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positive": tp,
        "gold_count": len(gold),
        "predicted_count": len(pred),
    }


def should_publish_probable(
    metrics: dict[str, Any],
    *,
    min_precision: float = 0.95,
) -> bool:
    """自动 probable 发布门禁：精度未达阈值时关闭，仅供人工审核。"""
    precision = metrics.get("precision", 0.0)
    return float(precision) >= min_precision
