"""Social integrity risk domain logic (07).

垃圾营销、机器人与协同行为的确定性核心：规则层特征（时间规律、重复
文本、营销词典、粉丝比）、单账号三分类风险评分（automation/marketing/
inauthenticity）、逆频率降权、账号-信号二部图协同检测。

原则：缺失字段用 unknown 而不是 0；官方同步/新闻转载等公共内容经逆频率
降权不误判为协同；相同输入与种子结果确定。
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import datetime
from typing import Any

# 风险类型与状态
RISK_TYPES = ("automation", "marketing", "inauthenticity")
RISK_BANDS = ("low", "medium", "high")
REVIEW_STATUSES = ("signal_only", "reviewed_likely", "reviewed_unlikely", "inconclusive")

# 营销/导流词典（弱信号）
_MARKETING_TERMS = (
    "加微信",
    "加v",
    "代购",
    "优惠券",
    "点击链接",
    "秒杀",
    "低价",
    "刷单",
    "兼职",
    "返利",
    "私聊",
    "进群",
)

_WHITESPACE_RE = re.compile(r"\s+")


def _norm_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    return _WHITESPACE_RE.sub("", normalized).lower()


def content_fingerprint(text: str) -> str:
    """规范化内容的稳定指纹（用于重复/模板检测）。"""
    normalized = _norm_text(text)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# ---- 时间规律 -------------------------------------------------------------


def interval_regularity(timestamps: list[datetime]) -> dict[str, Any]:
    """发布间隔规律性：间隔变异系数越小越规律。

    样本不足时返回 unknown（覆盖不足），而不是把缺失推断为 0。
    """
    ordered = sorted(t for t in timestamps if t is not None)
    if len(ordered) < 3:
        return {"value": None, "coverage": "insufficient"}
    intervals = [
        (ordered[i + 1] - ordered[i]).total_seconds()
        for i in range(len(ordered) - 1)
    ]
    mean = sum(intervals) / len(intervals)
    if mean <= 0:
        return {"value": 1.0, "coverage": "ok"}
    variance = sum((d - mean) ** 2 for d in intervals) / len(intervals)
    std = math.sqrt(variance)
    cv = std / mean
    # 规律性分数：cv 越小越规律（越接近固定间隔）。
    regularity = max(0.0, 1.0 - cv)
    return {"value": regularity, "coverage": "ok"}


def near_simultaneous_ratio(
    timestamps: list[datetime], window_seconds: int = 300
) -> dict[str, Any]:
    """近同时发布占比（时间桶协同信号）。"""
    ordered = sorted(t for t in timestamps if t is not None)
    if len(ordered) < 2:
        return {"value": None, "coverage": "insufficient"}
    close = 0
    for i in range(1, len(ordered)):
        if (ordered[i] - ordered[i - 1]).total_seconds() <= window_seconds:
            close += 1
    return {"value": close / (len(ordered) - 1), "coverage": "ok"}


# ---- 文本 / 营销 ----------------------------------------------------------


def duplicate_text_rate(posts: list[dict[str, Any]]) -> dict[str, Any]:
    """重复/模板文本占比。"""
    if not posts:
        return {"value": None, "coverage": "insufficient"}
    fingerprints = [content_fingerprint(str(p.get("content", ""))) for p in posts]
    non_empty = [f for f in fingerprints if f]
    if not non_empty:
        return {"value": None, "coverage": "insufficient"}
    unique = len(set(non_empty))
    return {"value": 1.0 - unique / len(non_empty), "coverage": "ok"}


def marketing_term_hits(text: str) -> list[str]:
    """营销/导流词典命中（弱信号，仅原因码，不直接定性）。"""
    normalized = _norm_text(text)
    return [term for term in _MARKETING_TERMS if term in normalized]


# ---- 账号特征 -------------------------------------------------------------


def follower_ratio_anomaly(account: dict[str, Any]) -> dict[str, Any]:
    """关注/粉丝异常比；缺失字段返回 unknown，不推断为 0。"""
    followers = account.get("follower_count")
    following = account.get("following_count")
    if followers is None or following is None:
        return {"value": None, "coverage": "unknown"}
    if not isinstance(followers, (int, float)) or not isinstance(following, (int, float)):
        return {"value": None, "coverage": "unknown"}
    if followers <= 0:
        # 平台隐藏粉丝数、新账号与真实 0 粉丝在采集结果中通常无法区分。
        # 这个值只能表示覆盖不确定，不能直接作为“自动化账号”的强证据。
        return {"value": None, "coverage": "ambiguous_zero"}
    ratio = following / followers
    # 关注远多于粉丝（如 >10:1）是异常。
    anomaly = min(1.0, ratio / 10.0)
    return {"value": anomaly, "coverage": "ok"}


# ---- 单账号风险评分 -------------------------------------------------------


def account_risk_assessment(
    account: dict[str, Any],
    posts: list[dict[str, Any]],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """单账号三分类风险：返回 automation/marketing/inauthenticity 分数、
    原因码、证据与 band。缺失数据不推断为 0。"""
    policy = policy or {}
    thresholds = policy.get("thresholds", {"medium": 0.4, "high": 0.7})
    reasons_by_risk: dict[str, list[str]] = {risk: [] for risk in RISK_TYPES}
    evidence_by_risk: dict[str, dict[str, Any]] = {
        risk: {} for risk in RISK_TYPES
    }

    timestamps = [
        p.get("published_at") for p in posts if isinstance(p.get("published_at"), datetime)
    ]
    regularity = interval_regularity(timestamps)
    if regularity["value"] is not None and regularity["value"] > 0.7:
        reasons_by_risk["automation"].append("regular_interval_posting")
        evidence_by_risk["automation"]["interval_regularity"] = regularity["value"]

    duplicate = duplicate_text_rate(posts)
    if duplicate["value"] is not None and duplicate["value"] > 0.5:
        reasons_by_risk["automation"].append("high_duplicate_text")
        reasons_by_risk["inauthenticity"].append("high_duplicate_text")
        evidence_by_risk["automation"]["duplicate_text_rate"] = duplicate["value"]
        evidence_by_risk["inauthenticity"]["duplicate_text_rate"] = duplicate["value"]

    marketing_hits: set[str] = set()
    for post in posts:
        marketing_hits.update(marketing_term_hits(str(post.get("content", ""))))
    if marketing_hits:
        reasons_by_risk["marketing"].append("marketing_terms")
        evidence_by_risk["marketing"]["marketing_terms"] = sorted(marketing_hits)

    ratio = follower_ratio_anomaly(account)
    if ratio["value"] is not None and ratio["value"] > 0.7:
        reasons_by_risk["inauthenticity"].append("follower_following_anomaly")
        evidence_by_risk["inauthenticity"]["follower_ratio_anomaly"] = ratio["value"]

    # 三分类分数（确定性规则聚合）。
    automation_score = _rule_score(
        [regularity["value"] or 0, duplicate["value"] or 0], [0.6, 0.4]
    )
    # 营销词典命中是弱信号：结合命中词数 + 命中帖子频率，
    # 单次命中一个词不直接判 high。
    marketing_post_count = sum(
        1
        for post in posts
        if marketing_term_hits(str(post.get("content", "")))
    )
    post_total = len(posts) if posts else 1
    marketing_ratio = marketing_post_count / post_total
    hit_count = len(marketing_hits)
    marketing_score = min(
        1.0,
        0.3 * min(1.0, hit_count / 3.0) + 0.5 * marketing_ratio,
    )
    # 单条内容即使命中多个导流词，也不足以把整个账号自动判为 high。
    if marketing_post_count < 2:
        marketing_score = min(
            marketing_score, float(thresholds.get("high", 0.7)) - 0.01
        )
    inauthenticity_score = _rule_score(
        [ratio["value"] or 0, duplicate["value"] or 0], [0.5, 0.5]
    )

    return {
        "scores": {
            "automation": round(automation_score, 3),
            "marketing": round(marketing_score, 3),
            "inauthenticity": round(inauthenticity_score, 3),
        },
        "reason_codes": sorted(
            {reason for reasons in reasons_by_risk.values() for reason in reasons}
        ),
        "evidence": {
            key: value
            for risk_evidence in evidence_by_risk.values()
            for key, value in risk_evidence.items()
        },
        "reason_codes_by_risk": reasons_by_risk,
        "evidence_by_risk": evidence_by_risk,
        "coverage": {
            "timestamps": len(timestamps),
            "posts": len(posts),
            "follower_count_known": account.get("follower_count") is not None,
        },
        "bands": {
            "automation": _band(automation_score, thresholds),
            "marketing": _band(marketing_score, thresholds),
            "inauthenticity": _band(inauthenticity_score, thresholds),
        },
    }


def _rule_score(values: list[float], weights: list[float]) -> float:
    total = 0.0
    weight_sum = 0.0
    for value, weight in zip(values, weights, strict=False):
        total += value * weight
        weight_sum += weight
    return total / weight_sum if weight_sum else 0.0


def _band(score: float, thresholds: dict[str, Any]) -> str:
    high = float(thresholds.get("high", 0.7))
    medium = float(thresholds.get("medium", 0.4))
    if score >= high:
        return "high"
    if score >= medium:
        return "medium"
    return "low"


# ---- 逆频率降权 + 二部图协同 ---------------------------------------------


def inverse_frequency_weight(
    signal: str,
    signal_counts: dict[str, int],
    total_accounts: int,
) -> float:
    """逆频率权重：越公共的信号（被越多账号使用）协同权重越低。

    官方同步公告、新闻转载等公共内容因此不会仅凭文本相似被定为协同。
    """
    if total_accounts <= 0:
        return 1.0
    df = signal_counts.get(signal, 1)
    idf = math.log((total_accounts + 1) / (df + 1))
    # 归一化到 [0,1]：df=1 时权重 1，df=total_accounts（全共享）时权重 0。
    max_idf = math.log((total_accounts + 1) / 2.0)
    if max_idf <= 0:
        return 0.0
    return idf / max_idf


def detect_coordination(
    account_posts: dict[str, list[dict[str, Any]]],
    *,
    min_support: int = 2,
    time_bucket_seconds: int = 300,
) -> list[dict[str, Any]]:
    """基于稀疏账号—语义信号图生成协同候选。

    时间接近只作为边的增强特征，不作为可与文本或链接等价计数的独立
    共享信号。候选对仅从共享文本或链接的倒排桶产生，避免账号全连接。
    """
    signals: dict[str, set[str]] = {}
    timestamps: dict[str, list[datetime]] = {}
    for account_id, posts in account_posts.items():
        account_signals: set[str] = set()
        account_times: list[datetime] = []
        for post in posts:
            fingerprint = content_fingerprint(str(post.get("content", "")))
            if fingerprint:
                account_signals.add(f"text:{fingerprint}")
            url = _normalize_link(str(post.get("url") or post.get("external_url") or ""))
            if url:
                account_signals.add(f"link:{url}")
            published = post.get("published_at")
            if isinstance(published, datetime):
                account_times.append(published)
        signals[account_id] = account_signals
        timestamps[account_id] = sorted(account_times)

    total_accounts = len(account_posts)
    if total_accounts < 2:
        return []

    inverted: dict[str, list[str]] = {}
    for account_id, account_signals in signals.items():
        for signal in account_signals:
            inverted.setdefault(signal, []).append(account_id)
    signal_counts = {signal: len(ids) for signal, ids in inverted.items()}

    pair_signals: dict[tuple[str, str], set[str]] = {}
    for signal, members in inverted.items():
        # 被多数账号共同使用的公告、新闻稿或热点标签不是有效协同阻塞键。
        if total_accounts >= 3 and len(members) / total_accounts > 0.5:
            continue
        ordered = sorted(set(members))
        for index, account_a in enumerate(ordered):
            for account_b in ordered[index + 1 :]:
                pair_signals.setdefault((account_a, account_b), set()).add(signal)

    parent = {account_id: account_id for account_id in account_posts}

    def find(account_id: str) -> str:
        while parent[account_id] != account_id:
            parent[account_id] = parent[parent[account_id]]
            account_id = parent[account_id]
        return account_id

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    qualifying: dict[tuple[str, str], dict[str, Any]] = {}
    for pair, shared in pair_signals.items():
        if len(shared) < min_support:
            continue
        account_a, account_b = pair
        weights = [
            inverse_frequency_weight(signal, signal_counts, total_accounts)
            for signal in shared
        ]
        semantic_score = sum(weights) / len(weights) if weights else 0.0
        if total_accounts == 2:
            semantic_score = max(semantic_score, 0.5)
        temporally_close = _has_temporal_overlap(
            timestamps[account_a], timestamps[account_b], time_bucket_seconds
        )
        score = min(1.0, semantic_score + (0.2 if temporally_close else 0.0))
        qualifying[pair] = {
            "signals": sorted(shared),
            "score": score,
            "temporally_close": temporally_close,
        }
        union(account_a, account_b)

    components: dict[str, set[str]] = {}
    for account_id in account_posts:
        components.setdefault(find(account_id), set()).add(account_id)

    results: list[dict[str, Any]] = []
    for members in components.values():
        if len(members) < 2:
            continue
        component_edges = {
            pair: data
            for pair, data in qualifying.items()
            if pair[0] in members and pair[1] in members
        }
        if not component_edges:
            continue
        shared_signals = sorted(
            {signal for data in component_edges.values() for signal in data["signals"]}
        )
        member_evidence: dict[str, list[dict[str, Any]]] = {
            member: [] for member in sorted(members)
        }
        for (left, right), data in component_edges.items():
            evidence = {
                "peer": right,
                "signals": data["signals"],
                "temporally_close": data["temporally_close"],
            }
            member_evidence[left].append(evidence)
            member_evidence[right].append({**evidence, "peer": left})
        results.append(
            {
                "account_ids": sorted(members),
                "size": len(members),
                "score": round(
                    sum(data["score"] for data in component_edges.values())
                    / len(component_edges),
                    3,
                ),
                "shared_signals": shared_signals,
                "evidence": member_evidence,
            }
        )
    return sorted(results, key=lambda item: (-item["score"], item["account_ids"]))


def _has_temporal_overlap(
    left: list[datetime], right: list[datetime], window_seconds: int
) -> bool:
    left_index = right_index = 0
    while left_index < len(left) and right_index < len(right):
        delta = (left[left_index] - right[right_index]).total_seconds()
        if abs(delta) <= window_seconds:
            return True
        if delta < 0:
            left_index += 1
        else:
            right_index += 1
    return False

def _normalize_link(url: str) -> str:
    parsed = str(url or "").strip()
    if not parsed:
        return ""
    if "://" in parsed:
        scheme, rest = parsed.split("://", 1)
        return f"{scheme.lower()}://{rest.split('?', 1)[0].lower()}"
    return parsed.lower()
