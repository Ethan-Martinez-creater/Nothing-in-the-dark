"""P0-1.5: production traffic goes through Agent Message / Agent Run only."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database import Database
from app.main import create_app
from app.schemas.cases import CreateCaseRequest
from app.schemas.tasks import StartAnalysisRequest


def test_analysis_compat_returns_agent_run_not_task(tmp_path: Path) -> None:
    """POST /analysis is a thin shim: it must start an Agent Run."""
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'entry.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case_id = client.post(
            "/api/v1/cases",
            json={"topic": "生产入口", "platforms": ["weibo"]},
        ).json()["id"]
        response = client.post(
            f"/api/v1/cases/{case_id}/analysis",
            json={"include_fact_check": True, "max_budget": 5},
        )
        assert response.status_code == 202
        body = response.json()
        assert "agent" in body
        assert body["case_id"] == case_id
        assert "current_stage" not in body
        assert response.headers.get("deprecation") == "true"
        run = client.get(f"/api/v1/runs/{body['id']}")
        assert run.status_code == 200
        assert run.json()["id"] == body["id"]


def test_capabilities_declare_messages_as_production_entry(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'caps.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        payload = client.get("/api/v1/system/capabilities").json()
    assert payload["production_entry"] == "messages"
    assert payload["legacy_analysis"] is False
    assert payload["durable_checkpointer"] in {"postgresql", "memory"}
    assert "llm_configured" in payload
    assert payload["llm_configured"] == payload["llm"]["configured"]


def test_app_start_does_not_recover_legacy_analysis_runner(tmp_path: Path) -> None:
    """Leftover AnalysisTask rows must not be executed by CaseAnalysisGraph
    when the API process starts. Production artifacts come only from Runs."""
    db_path = tmp_path / "no-legacy.db"

    async def seed() -> str:
        database = Database(f"sqlite+aiosqlite:///{db_path}")
        await database.create_schema()
        repository = ApplicationRepository(database)
        case = await repository.create_case(
            CreateCaseRequest(topic="遗留任务不得复活", platforms=["weibo"])
        )
        await repository.create_task(case.id, StartAnalysisRequest())
        await database.dispose()
        return case.id

    case_id = asyncio.run(seed())
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True)
    )
    with TestClient(app) as client:
        # The legacy graph sleeps ~0.12s per node; wait long enough that a
        # recovered runner would have finished, then assert it did not run.
        last_status = None
        for _ in range(20):
            tasks = client.get(f"/api/v1/cases/{case_id}/tasks").json()
            last_status = tasks[0]["status"]
            if last_status == "completed":
                break
            time.sleep(0.15)
        artifacts = client.get(f"/api/v1/cases/{case_id}/artifacts").json()
    assert last_status == "pending"
    assert artifacts == []
