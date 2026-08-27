"""P0-1.1e: temporal / subject / context consistency on fact-check cards."""

from __future__ import annotations

from app.services.analysis import verify_claims


async def test_verify_claims_records_consistency_fields() -> None:
    posts = [
        {
            "id": "p1",
            "platform": "weibo",
            "author": "通报账号",
            "content": "官方回应称2026年8月1日事故伤亡数据失实，正在调查",
            "published_at": "2026-08-01T09:00:00+00:00",
        }
    ]
    result = await verify_claims(posts, "事故调查")
    card = result["cards"][0]
    assert card["temporal_consistency"] in {"pass", "fail", "unknown"}
    assert card["subject_consistency"] in {"pass", "fail", "unknown"}
    assert card["context_consistency"] in {"pass", "fail", "unknown"}
    assert "temporal_consistency" in result["verification_checks"]
    assert "subject_consistency" in result["verification_checks"]
    assert "context_consistency" in result["verification_checks"]


async def test_context_consistency_passes_when_claim_is_in_source() -> None:
    posts = [
        {
            "id": "p1",
            "platform": "weibo",
            "author": "记者",
            "content": "官方回应称事故伤亡数据失实，正在调查。请以通报为准。",
            "published_at": "2026-08-01T09:00:00+00:00",
        }
    ]
    result = await verify_claims(posts, "事故调查")
    assert result["cards"][0]["context_consistency"] == "pass"


async def test_temporal_consistency_fails_when_claim_date_predates_post() -> None:
    posts = [
        {
            "id": "p1",
            "platform": "weibo",
            "author": "记者",
            "content": "官方回应称2024年1月1日事故伤亡数据失实，正在调查",
            "published_at": "2026-08-01T09:00:00+00:00",
        }
    ]
    result = await verify_claims(posts, "事故调查")
    assert result["cards"][0]["temporal_consistency"] == "fail"
    assert "temporal_mismatch" in result["cards"][0]["checks"]


async def test_subject_consistency_flags_when_topic_entities_missing() -> None:
    posts = [
        {
            "id": "p1",
            "platform": "weibo",
            "author": "记者",
            "content": "有人声称金额达¥999999元已经到账",
            "published_at": "2026-08-01T09:00:00+00:00",
        }
    ]
    result = await verify_claims(posts, "校园球赛")
    assert result["cards"][0]["subject_consistency"] in {"fail", "unknown"}
