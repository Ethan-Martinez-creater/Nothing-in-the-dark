from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.schemas.tasks import ArtifactResponse


def test_health_capabilities_and_case_creation(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        capabilities = client.get("/api/v1/system/capabilities")
        created = client.post(
            "/api/v1/cases",
            json={
                "topic": "测试舆情案例",
                "description": "API integration test",
                "platforms": ["weibo", "bilibili"],
            },
        )

    assert health.status_code == 200
    assert health.json()["status"] == "healthy"
    assert capabilities.status_code == 200
    assert capabilities.json()["framework"] == "langgraph"
    assert created.status_code == 201
    assert created.json()["topic"] == "测试舆情案例"


def test_real_crawl_requires_explicit_approval(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'approval.db'}",
            demo_mode=False,
        )
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/cases",
            json={"topic": "真实采集审批测试", "platforms": ["weibo"]},
        )
        started = client.post(
            f"/api/v1/cases/{created.json()['id']}/analysis",
            json={"force_crawl": False},
        )

    assert started.status_code == 400
    assert started.json()["code"] == "crawl_approval_required"


def test_artifact_response_exposes_run_id(tmp_path: Path) -> None:
    """ArtifactResponse 必须透传 run_id，前端才能把 Artifact 挂到 Run 下。"""
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'artifact-run.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/cases",
            json={"topic": "Artifact run_id", "platforms": ["weibo"]},
        )
        case_id = created.json()["id"]
        run = client.post(
            f"/api/v1/cases/{case_id}/messages",
            json={"content": "请分析该案例"},
        ).json()
        run_id = run["id"]

        repo = app.state.container.repository

        async def _seed() -> None:
            await repo.create_artifact(
                case_id=case_id,
                run_id=run_id,
                kind="report",
                title="测试报告",
                data={"title": "t"},
            )

        import asyncio

        asyncio.run(_seed())

        artifacts = client.get(f"/api/v1/cases/{case_id}/artifacts")

    assert artifacts.status_code == 200
    assert artifacts.json()[0]["run_id"] == run_id


def test_artifact_response_schema_serializes_run_id() -> None:
    from types import SimpleNamespace

    record = SimpleNamespace(
        id="a1",
        case_id="c1",
        task_id=None,
        run_id="r1",
        kind="report",
        title="t",
        version=1,
        data={},
        created_at=datetime.now(UTC),
    )
    response = ArtifactResponse.model_validate(record)
    assert response.run_id == "r1"


def test_list_case_runs_returns_all_runs_in_order(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'case-runs.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/cases",
            json={"topic": "Runs 列表", "platforms": ["weibo"]},
        )
        case_id = created.json()["id"]
        first = client.post(
            f"/api/v1/cases/{case_id}/messages",
            json={"content": "第一轮分析"},
        )
        second = client.post(
            f"/api/v1/cases/{case_id}/messages",
            json={"content": "第二轮追问"},
        )
        listed = client.get(f"/api/v1/cases/{case_id}/runs")

    assert first.status_code == 202
    assert second.status_code == 202
    assert listed.status_code == 200
    runs = listed.json()
    assert [r["id"] for r in runs] == [first.json()["id"], second.json()["id"]]
    assert runs[0]["turn_id"] is not None
    assert runs[0]["objective"] == "第一轮分析"
    assert runs[1]["objective"] == "第二轮追问"


def test_sse_resumes_from_cursor(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'sse.db'}",
            demo_mode=True,
            event_poll_interval_seconds=0.05,
        )
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/cases",
            json={"topic": "SSE 续传", "platforms": ["weibo"]},
        )
        case_id = created.json()["id"]
        run = client.post(
            f"/api/v1/cases/{case_id}/messages",
            json={"content": "分析"},
        ).json()
        run_id = run["id"]
        repo = app.state.container.repository

        async def _seed_events() -> None:
            payload = {
                "event_type": "agent_queued",
                "agent": "coordinator",
                "status": "pending",
            }
            await repo.add_run_event(run_id, payload)
            await repo.add_run_event(run_id, payload)
            await repo.update_agent_run(run_id, status="completed")

        import asyncio

        asyncio.run(_seed_events())

        # 默认从头开始：第一条事件 id 最小
        with client.stream(
            "GET", f"/api/v1/runs/{run_id}/events/stream"
        ) as resp:
            first_line = next(resp.iter_lines())
        assert "id: 1" in first_line

        # cursor=1 从第二条开始
        with client.stream(
            "GET", f"/api/v1/runs/{run_id}/events/stream?cursor=1"
        ) as resp:
            first_line = next(resp.iter_lines())
        assert "id: 2" in first_line

        # Last-Event-ID 头同样生效
        with client.stream(
            "GET",
            f"/api/v1/runs/{run_id}/events/stream",
            headers={"Last-Event-ID": "1"},
        ) as resp:
            first_line = next(resp.iter_lines())
        assert "id: 2" in first_line
