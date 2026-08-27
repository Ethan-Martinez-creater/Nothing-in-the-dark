"""Tests for integrity risk detection (07)."""

from __future__ import annotations

import asyncio
import atexit
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.database.integrity_repository import IntegrityRepository
from app.main import create_app
from app.schemas.cases import CreateCaseRequest
from app.services import integrity

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-integrity-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


def _t(offset_seconds: int) -> datetime:
    return datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=offset_seconds)


# ---------- 规则层特征 -----------------------------------------------------


def test_interval_regularity_insufficient() -> None:
    assert integrity.interval_regularity([])["value"] is None
    assert integrity.interval_regularity([_t(0), _t(60)])["value"] is None


def test_interval_regularity_detects_regular() -> None:
    timestamps = [_t(i * 60) for i in range(6)]
    assert integrity.interval_regularity(timestamps)["value"] > 0.9


def test_duplicate_text_rate() -> None:
    posts = [
        {"content": "相同内容"},
        {"content": "相同内容"},
        {"content": "不同内容"},
    ]
    assert abs(integrity.duplicate_text_rate(posts)["value"] - 1.0 / 3.0) < 1e-9


def test_follower_ratio_missing_is_unknown() -> None:
    assert integrity.follower_ratio_anomaly({})["coverage"] == "unknown"
    assert integrity.follower_ratio_anomaly({"follower_count": 100})["coverage"] == "unknown"


def test_marketing_term_hits() -> None:
    assert "加微信" in integrity.marketing_term_hits("加微信详聊")
    assert integrity.marketing_term_hits("普通讨论") == []


# ---------- 单账号风险 -----------------------------------------------------


def test_account_risk_marketing() -> None:
    result = integrity.account_risk_assessment(
        {"follower_count": 100},
        [{"content": "加微信代购优惠券", "published_at": _t(0)}],
    )
    assert result["scores"]["marketing"] > 0.5
    assert "marketing_terms" in result["reason_codes"]


def test_account_risk_missing_data_not_zero() -> None:
    # 无帖子、无粉丝字段：coverage 不足，不把缺失推断为 0 风险。
    result = integrity.account_risk_assessment({}, [])
    assert result["coverage"]["posts"] == 0
    assert result["coverage"]["follower_count_known"] is False


# ---------- 逆频率 + 协同 --------------------------------------------------


def test_inverse_frequency_demotes_common() -> None:
    common = integrity.inverse_frequency_weight("s", {"s": 10}, 10)
    rare = integrity.inverse_frequency_weight("s", {"s": 1}, 10)
    assert rare > common


def test_detect_coordination_detects_shared() -> None:
    account_posts = {
        "a1": [{"content": "独特文案X", "published_at": _t(0), "url": "https://x.com/u1"}],
        "a2": [{"content": "独特文案X", "published_at": _t(1), "url": "https://x.com/u1"}],
    }
    clusters = integrity.detect_coordination(account_posts, min_support=2)
    assert len(clusters) >= 1
    assert clusters[0]["size"] == 2


def test_detect_coordination_no_signal() -> None:
    account_posts = {
        "a1": [{"content": "无关文案A", "published_at": _t(0)}],
        "a2": [{"content": "无关文案B", "published_at": _t(10000)}],
    }
    assert integrity.detect_coordination(account_posts, min_support=2) == []


# ---------- 仓储 -----------------------------------------------------------


async def _setup_repo(db_path: Path) -> tuple[IntegrityRepository, str]:
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="完整性测试", platforms=["weibo"]))
    return IntegrityRepository(database), case.id


async def test_upsert_risk_assessment_idempotent() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    first = await repo.upsert_risk_assessment(
        case_id=case_id, subject_type="account", subject_id="a1",
        risk_type="automation", score=0.8, band="high",
    )
    second = await repo.upsert_risk_assessment(
        case_id=case_id, subject_type="account", subject_id="a1",
        risk_type="automation", score=0.9, band="high",
    )
    assert first.id == second.id
    assert second.score == 0.9


async def test_cluster_and_members() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    cluster = await repo.create_cluster(
        case_id=case_id, size=2, score=0.8,
        members=[
            {"account_id": "a1", "score": 0.8, "evidence": ["s1"]},
            {"account_id": "a2", "score": 0.8, "evidence": ["s1"]},
        ],
    )
    members = await repo.list_cluster_members(cluster.id)
    assert len(members) == 2


async def test_review_assessment() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    assessment = await repo.upsert_risk_assessment(
        case_id=case_id, subject_type="account", subject_id="a1",
        risk_type="marketing", score=0.6, band="medium",
    )
    reviewed = await repo.review_assessment(assessment.id, "reviewed_unlikely", by="reviewer")
    assert reviewed.status == "reviewed_unlikely"
    assert reviewed.reviewed_by == "reviewer"


# ---------- API -----------------------------------------------------------


def test_api_integrity_assessments() -> None:
    db_path = _tmp_db()
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    asyncio.run(database.create_schema())
    app_repo = ApplicationRepository(database)

    async def seed() -> str:
        case = await app_repo.create_case(
            CreateCaseRequest(topic="完整性 API", platforms=["weibo"])
        )
        repo = IntegrityRepository(database)
        await repo.upsert_risk_assessment(
            case_id=case.id, subject_type="account", subject_id="a1",
            risk_type="automation", score=0.9, band="high",
        )
        return case.id

    case_id = asyncio.run(seed())
    asyncio.run(database.dispose())

    app = create_app(Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True))
    with TestClient(app) as client:
        response = client.get(f"/api/v1/cases/{case_id}/integrity/assessments")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 1
        assert payload[0]["risk_type"] == "automation"
        # review 端点。
        reviewed = client.post(
            f"/api/v1/cases/{case_id}/integrity/assessments/{payload[0]['id']}:review",
            json={"status": "reviewed_unlikely"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["status"] == "reviewed_unlikely"


def test_zero_followers_is_ambiguous_not_automation_evidence() -> None:
    feature = integrity.follower_ratio_anomaly(
        {"follower_count": 0, "following_count": 1000}
    )
    assert feature["value"] is None
    assert feature["coverage"] == "ambiguous_zero"


def test_single_weak_marketing_hit_cannot_be_high() -> None:
    result = integrity.account_risk_assessment(
        {"follower_count": 10, "following_count": 10},
        [{"content": "这是优惠券使用说明", "published_at": _t(0)}],
    )
    assert result["bands"]["marketing"] != "high"
    assert result["reason_codes_by_risk"]["automation"] == []
    assert result["reason_codes_by_risk"]["marketing"] == ["marketing_terms"]


def test_public_signal_is_not_coordination_and_time_is_not_independent_signal() -> None:
    common = {
        f"a{i}": [
            {
                "content": "所有媒体同步发布的公共公告",
                "published_at": _t(i),
                "url": "https://official.example/announcement",
            }
        ]
        for i in range(6)
    }
    assert integrity.detect_coordination(common, min_support=2) == []

    only_time = {
        "x": [{"content": "内容甲", "published_at": _t(0)}],
        "y": [{"content": "内容乙", "published_at": _t(1)}],
    }
    assert integrity.detect_coordination(only_time, min_support=1) == []


def test_coordination_components_merge_transitively_once() -> None:
    posts = {
        "a": [
            {"content": "共享甲", "url": "https://x.test/one", "published_at": _t(0)},
            {"content": "共享乙", "url": "https://x.test/two", "published_at": _t(10)},
        ],
        "b": [
            {"content": "共享甲", "url": "https://x.test/one", "published_at": _t(1)},
            {"content": "共享丙", "url": "https://x.test/three", "published_at": _t(20)},
        ],
        "c": [
            {"content": "共享丙", "url": "https://x.test/three", "published_at": _t(21)},
            {"content": "共享丁", "url": "https://x.test/four", "published_at": _t(30)},
        ],
        "noise": [{"content": "无关", "published_at": _t(9999)}],
    }
    clusters = integrity.detect_coordination(posts, min_support=2)
    assert len(clusters) == 1
    assert clusters[0]["account_ids"] == ["a", "b", "c"]
