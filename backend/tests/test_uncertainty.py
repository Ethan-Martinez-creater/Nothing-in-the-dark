"""Tests for uncertainty & bias (08)."""

from __future__ import annotations

import asyncio
import atexit
import shutil
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.database.uncertainty_repository import UncertaintyRepository
from app.main import create_app
from app.schemas.cases import CreateCaseRequest
from app.services import uncertainty

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-uncertainty-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


# ---------- 置信组合 -------------------------------------------------------


def test_combine_confidence_all_high() -> None:
    level, reasons = uncertainty.combine_confidence(
        {"coverage": "high", "evidence_strength": "high", "model_uncertainty": "high"}
    )
    assert level == "high"
    assert reasons == []


def test_combine_confidence_conservative() -> None:
    level, _ = uncertainty.combine_confidence(
        {"coverage": "high", "evidence_strength": "low", "model_uncertainty": "high"}
    )
    assert level == "low"


def test_combine_confidence_key_insufficient_caps_low() -> None:
    # 关键维度 insufficient：整体不得高于 low。
    level, reasons = uncertainty.combine_confidence(
        {"coverage": "insufficient", "evidence_strength": "high", "model_uncertainty": "high"}
    )
    assert level in ("insufficient", "low")
    assert "coverage_insufficient" in reasons


def test_combine_confidence_no_dimensions() -> None:
    level, reasons = uncertainty.combine_confidence({})
    assert level == "insufficient"
    assert "no_dimensions" in reasons


# ---------- 证据独立性 -----------------------------------------------------


def test_group_evidence_same_source_not_independent() -> None:
    evidence = [
        {"source_url": "https://a.com/p1", "text": "转载1"},
        {"source_url": "https://a.com/p1", "text": "转载2"},
        {"source_url": "https://b.com/p2", "text": "独立"},
    ]
    assert uncertainty.independent_evidence_count(evidence) == 2


# ---------- 报告用语 -------------------------------------------------------


def test_assert_no_strong_claim_low() -> None:
    assert "证实" in uncertainty.assert_no_strong_claim("该结论已被证实", "low")
    assert uncertainty.assert_no_strong_claim("该结论可能成立", "low") == []


def test_assert_no_strong_claim_high_allows() -> None:
    assert uncertainty.assert_no_strong_claim("该结论已被证实", "high") == []


def test_format_score_uncalibrated() -> None:
    assert "uncalibrated_score" in uncertainty.format_score(0.87, calibrated=False)
    assert uncertainty.format_score(0.87, calibrated=True) == "0.87"


# ---------- 敏感性 ---------------------------------------------------------


def test_sensitivity_difference() -> None:
    diff = uncertainty.sensitivity_difference(
        {"posts": 100, "positive_ratio": 0.6},
        {"posts": 100, "positive_ratio": 0.5},
    )
    assert abs(diff["positive_ratio"]["delta"] - (-0.1)) < 1e-9
    assert "posts" not in diff  # 无变化不记录


# ---------- 仓储 -----------------------------------------------------------


async def _setup_repo(db_path: Path) -> tuple[UncertaintyRepository, str]:
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="不确定性测试", platforms=["weibo"]))
    return UncertaintyRepository(database), case.id


async def test_quality_assessment_upsert() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    first = await repo.upsert_quality_assessment(
        case_id=case_id, target_type="opinion", target_id="o1",
        dimension="coverage", level="low",
    )
    second = await repo.upsert_quality_assessment(
        case_id=case_id, target_type="opinion", target_id="o1",
        dimension="coverage", level="medium",
    )
    assert first.id == second.id
    assert second.level == "medium"


async def test_sensitivity_run_idempotent() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    first = await repo.create_sensitivity_run(
        case_id=case_id, baseline_hash="hash1",
        baseline_params={"a": 1}, variant_params={"a": 2}, output_diff={"a": {"delta": 1}},
    )
    second = await repo.create_sensitivity_run(
        case_id=case_id, baseline_hash="hash1",
        baseline_params={"a": 1}, variant_params={"a": 2}, output_diff={},
    )
    assert first is not None
    assert second is None  # 幂等


async def test_hypothesis_create() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    record = await repo.create_hypothesis(
        case_id=case_id,
        statement="该传播可能由官方同步导致",
        prediction="若为官方同步，则公告时间集中且来源权威",
        supporting_evidence=["e1"],
        opposing_evidence=["e2"],
    )
    assert record.status == "proposed"
    hypotheses = await repo.list_hypotheses(case_id)
    assert len(hypotheses) == 1


# ---------- API -----------------------------------------------------------


def test_api_quality_and_hypotheses() -> None:
    db_path = _tmp_db()
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    asyncio.run(database.create_schema())
    app_repo = ApplicationRepository(database)

    async def seed() -> str:
        case = await app_repo.create_case(
            CreateCaseRequest(topic="不确定性 API", platforms=["weibo"])
        )
        return case.id

    case_id = asyncio.run(seed())
    asyncio.run(database.dispose())

    app = create_app(Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True))
    with TestClient(app) as client:
        combined = client.post(
            f"/api/v1/cases/{case_id}/quality/combine",
            json={"dimensions": {"coverage": "insufficient", "evidence_strength": "high"}},
        )
        assert combined.status_code == 200
        assert combined.json()["final_level"] in ("insufficient", "low")

        created = client.post(
            f"/api/v1/cases/{case_id}/hypotheses",
            json={"statement": "替代解释：官方同步导致"},
        )
        assert created.status_code == 201
        listed = client.get(f"/api/v1/cases/{case_id}/hypotheses")
        assert listed.status_code == 200
        assert len(listed.json()) == 1
