"""P0-1.1f: human review of a claim writes evaluation and updates status."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database import Database
from app.main import create_app
from app.schemas.cases import CreateCaseRequest


async def _seed(db_path: Path) -> tuple[str, str]:
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="人工复核", platforms=["weibo"])
    )
    run = await repository.create_agent_run(
        case_id=case.id, turn_id=None, objective="核查", metadata={}
    )
    claim = await repository.create_claim(
        case_id=case.id, text="官方尚未通报", created_by_run_id=run.id
    )
    return case.id, claim.id


async def test_review_claim_confirms_and_records_evaluation(tmp_path: Path) -> None:
    case_id, claim_id = await _seed(tmp_path / "review.db")
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'review.db'}")
    repository = ApplicationRepository(database)
    updated = await repository.review_claim(
        case_id, claim_id, confirmed=True, note="人工核对属实"
    )
    assert updated.status == "human_confirmed"
    records = await repository.list_evaluations(
        case_id=case_id, metric="claim_human_review"
    )
    assert len(records) == 1
    assert records[0].score == 1.0
    assert records[0].details["claim_id"] == claim_id


async def test_review_claim_rejects(tmp_path: Path) -> None:
    case_id, claim_id = await _seed(tmp_path / "review-reject.db")
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'review-reject.db'}")
    repository = ApplicationRepository(database)
    updated = await repository.review_claim(
        case_id, claim_id, confirmed=False, note="证据不足"
    )
    assert updated.status == "human_rejected"
    records = await repository.list_evaluations(
        case_id=case_id, metric="claim_human_review"
    )
    assert records[0].score == 0.0


def test_review_claim_api(tmp_path: Path) -> None:
    db_path = tmp_path / "review-api.db"
    case_id, claim_id = asyncio.run(_seed(db_path))
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True)
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/cases/{case_id}/claims/{claim_id}/review",
            json={"confirmed": True, "note": "属实"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "human_confirmed"
        assert body["id"] == claim_id
