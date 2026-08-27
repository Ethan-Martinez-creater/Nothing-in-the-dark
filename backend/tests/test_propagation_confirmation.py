"""M2: propagation edge human confirmation — repository flip + API endpoint.

``propagation_edges.human_confirmed`` 列早已存在（M7a），本次补齐确认端点
与可审计的 evaluations 记录。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database import Database
from app.main import create_app
from app.schemas.cases import CreateCaseRequest


def _edge_kwargs() -> dict:
    return {
        "source_post_id": "p1",
        "target_post_id": "p2",
        "relation": "observed",
        "confidence": 0.85,
        "feature_scores": {"time_decay": 0.9},
        "evidence_ids": ["p1", "p2"],
        "algorithm_version": "1.1.0",
    }


# ---------- repository layer ----------


async def _setup(tmp_path: Path) -> tuple[ApplicationRepository, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'confirmation.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="传播确认测试", platforms=["weibo", "bilibili"])
    )
    return repository, case.id


async def test_confirm_flips_human_confirmed_and_appends_evaluation(
    tmp_path: Path,
) -> None:
    repository, case_id = await _setup(tmp_path)
    edge = await repository.create_propagation_edge(case_id=case_id, **_edge_kwargs())
    assert edge.human_confirmed is False

    confirmed = await repository.confirm_propagation_edge(
        case_id, edge.id, confirmed=True, note="人工复核通过"
    )
    assert confirmed.human_confirmed is True

    evaluations = await repository.list_evaluations(case_id=case_id)
    assert len(evaluations) == 1
    assert evaluations[0].metric == "propagation_edge_human_confirmation"
    assert evaluations[0].score == 1.0
    assert evaluations[0].details == {"edge_id": edge.id, "note": "人工复核通过"}

    # 可翻转回未确认。
    reverted = await repository.confirm_propagation_edge(
        case_id, edge.id, confirmed=False
    )
    assert reverted.human_confirmed is False


async def test_confirm_unknown_edge_raises(tmp_path: Path) -> None:
    repository, case_id = await _setup(tmp_path)
    await repository.create_propagation_edge(case_id=case_id, **_edge_kwargs())
    try:
        await repository.confirm_propagation_edge(
            case_id, "no-such-edge", confirmed=True
        )
        raise AssertionError("expected ResourceNotFoundError")
    except Exception as exc:
        assert "propagation edge" in str(exc)


async def test_confirm_edge_belongs_to_other_case_rejected(tmp_path: Path) -> None:
    repository, case_id = await _setup(tmp_path)
    other = await repository.create_case(
        CreateCaseRequest(topic="另一个案例", platforms=["weibo"])
    )
    edge = await repository.create_propagation_edge(case_id=case_id, **_edge_kwargs())
    try:
        await repository.confirm_propagation_edge(
            other.id, edge.id, confirmed=True
        )
        raise AssertionError("expected ResourceNotFoundError")
    except Exception as exc:
        assert "propagation edge" in str(exc)


# ---------- API layer ----------


async def _seed_edge(db_path: Path) -> tuple[str, str]:
    """Pre-create a case and an observed edge in the shared SQLite file."""
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="传播确认", platforms=["weibo"])
        )
        edge = await repository.create_propagation_edge(
            case_id=case.id, **_edge_kwargs()
        )
        return case.id, edge.id
    finally:
        await database.dispose()


def test_api_confirm_propagation_edge(tmp_path: Path) -> None:
    db_path = tmp_path / "confirmation_api.db"
    case_id, edge_id = asyncio.run(_seed_edge(db_path))
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True)
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/cases/{case_id}/propagation-edges/{edge_id}/confirmation",
            json={"confirmed": True, "note": "API 层复核"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["human_confirmed"] is True
        assert payload["relation"] == "observed"
        assert payload["case_id"] == case_id
        assert payload["id"] == edge_id


def test_api_confirm_unknown_edge_returns_404(tmp_path: Path) -> None:
    db_path = tmp_path / "confirmation_api.db"
    case_id, _ = asyncio.run(_seed_edge(db_path))
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True)
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/cases/{case_id}/propagation-edges/nope/confirmation",
            json={"confirmed": True},
        )
        assert response.status_code == 404
