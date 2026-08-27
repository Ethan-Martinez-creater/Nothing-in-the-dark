"""Verification 增强（2026-08-07）：主张规范化去重 + 跨平台一致性。

- ``_normalize_claim_key``：NFKC + 小写 + 去标点空白，全角/半角、大小写、
  空白变体折叠为同一键；
- ``verify_claims``：规范化去重（保留首条，`deduped` 计数）、跨平台一致性
  （同一主张在 ≥2 个平台出现 → `cross_platform` check + 置信度 +0.05，
  上限 0.9）。
"""

from __future__ import annotations

from app.services.analysis import _normalize_claim_key, verify_claims


def _post(post_id: str, content: str, *, platform: str = "weibo") -> dict:
    return {
        "id": post_id,
        "platform": platform,
        "author": "路人甲",
        "content": content,
        "published_at": "2026-08-01T00:00:00+00:00",
    }


# ---------- normalization ----------


def test_normalize_claim_key_collapses_variants() -> None:
    assert _normalize_claim_key("官方回应称事故伤亡数据失实，正在调查") == (
        _normalize_claim_key("官方回应称事故伤亡数据失实,正在调查")
    )
    assert _normalize_claim_key("AB事故数据失实") == _normalize_claim_key("ab事故数据失实")
    assert _normalize_claim_key("  官方 回应\n称事故！") == (
        _normalize_claim_key("官方回应称事故")
    )


async def test_verify_claims_dedups_normalized_variants() -> None:
    """同一主张的全角/半角/大小写变体只出一张卡，deduped 计数准确。"""
    posts = [
        _post("p1", "官方回应称事故伤亡数据失实，正在调查"),
        _post("p2", "官方回应称事故伤亡数据失实,正在调查"),
        _post("p3", "官方回应称事故伤亡数据失实 正在调查"),
    ]
    result = await verify_claims(posts, "事故调查")
    assert len(result["cards"]) == 1
    assert result["verification_checks"]["deduped"] == 2
    assert result["claim_extraction"]["candidate_count"] == 1


async def test_verify_claims_keeps_distinct_claims() -> None:
    """不同主张不受影响，deduped 保持 0。"""
    posts = [
        _post("p1", "官方回应称事故伤亡数据失实，正在调查"),
        _post("p2", "网友质疑调查进展缓慢，要求公开数据"),
    ]
    result = await verify_claims(posts, "事故调查")
    assert len(result["cards"]) == 2
    assert result["verification_checks"]["deduped"] == 0


# ---------- cross-platform agreement ----------


async def test_cross_platform_agreement_flags_check_and_boosts() -> None:
    """同一主张出现在两个平台 → cross_platform check + 置信度上调。"""
    posts = [
        _post("p1", "官方回应称事故伤亡数据失实，正在调查", platform="weibo"),
        _post("p2", "官方回应称事故伤亡数据失实，正在调查", platform="bilibili"),
    ]
    result = await verify_claims(posts, "事故调查")
    card = result["cards"][0]
    assert "cross_platform" in card["checks"]
    assert card["confidence"] == 0.55  # insufficient 0.5 + 0.05
    assert "跨平台语境一致" in card["reason"]
    assert result["verification_checks"]["cross_platform"] == 1


async def test_single_platform_has_no_cross_platform_check() -> None:
    posts = [
        _post("p1", "官方回应称事故伤亡数据失实，正在调查", platform="weibo"),
        _post("p2", "官方回应称事故伤亡数据失实，正在调查", platform="weibo"),
    ]
    result = await verify_claims(posts, "事故调查")
    card = result["cards"][0]
    assert "cross_platform" not in card["checks"]
    assert card["confidence"] == 0.5
    assert result["verification_checks"]["cross_platform"] == 0


async def test_cross_platform_confidence_stays_bounded() -> None:
    """跨平台加成不突破 0.9 上限（credible 0.6 + 加成）。"""
    posts = [
        _post("p1", "官方回应称事故伤亡数据失实，正在调查", platform="weibo"),
        _post("p2", "官方回应称事故伤亡数据失实，正在调查", platform="bilibili"),
        _post("p3", "官方回应称事故伤亡数据失实，正在调查", platform="zhihu"),
        _post("p4", "官方回应称事故伤亡数据失实，正在调查", platform="douyin"),
        _post("p5", "官方回应称事故伤亡数据失实，正在调查", platform="tieba"),
    ]
    result = await verify_claims(posts, "事故调查")
    card = result["cards"][0]
    assert card["confidence"] == 0.55  # 仅一次加成，不随平台数叠加
    assert result["verification_checks"]["cross_platform"] == 1
