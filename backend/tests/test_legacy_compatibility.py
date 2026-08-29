"""Optimization V2 M0.2：legacy 兼容性快照。

在 M1（前端 Router/Shell 重构）和 M2（UiContext 注入）改动前，把本轮会触碰的
后端主链行为固定下来：messages -> run -> events。monitor alert 状态机、review
submit/decide、report artifact download 已由 test_monitoring.py / test_review.py /
test_reports.py 覆盖并计入基线（docs/optimization-v2-baseline.md），此处不重复。

这些断言在 M1-M8 全程必须保持绿色；任何破坏都意味着兼容性回归。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'legacy_compat.db'}",
            demo_mode=True,
        )
    )
    return TestClient(app)


def test_message_run_events_chain_compat(tmp_path: Path) -> None:
    """POST messages -> GET run -> GET events 的兼容链路。"""
    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/cases",
            json={
                "topic": "兼容性快照案例",
                "platforms": ["weibo"],
            },
        )
        assert created.status_code == 201, created.text
        case_id = created.json()["id"]

        started = client.post(
            f"/api/v1/cases/{case_id}/messages",
            json={"content": "请分析当前舆情态势", "approve_crawl": False},
        )
        assert started.status_code == 202, started.text
        run = started.json()
        assert run["case_id"] == case_id
        assert run["objective"] == "请分析当前舆情态势"
        # metadata 现有契约：approve_crawl 必须持久化
        assert run["metadata_json"]["approve_crawl"] is False

        run_id = run["id"]
        fetched = client.get(f"/api/v1/runs/{run_id}")
        assert fetched.status_code == 200

        events = client.get(f"/api/v1/runs/{run_id}/events")
        assert events.status_code == 200
        event_types = [event["event_type"] for event in events.json()]
        assert "agent_queued" in event_types


def test_legacy_case_routes_shape_compat(tmp_path: Path) -> None:
    """Case 列表 / 详情 / runs 列表的路由形状在 M1 前后保持不变。"""
    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/cases",
            json={"topic": "路由形状快照", "platforms": ["weibo", "zhihu"]},
        )
        assert created.status_code == 201
        case_id = created.json()["id"]

        listed = client.get("/api/v1/cases")
        assert listed.status_code == 200
        assert any(item["id"] == case_id for item in listed.json())

        detail = client.get(f"/api/v1/cases/{case_id}")
        assert detail.status_code == 200
        assert detail.json()["id"] == case_id
        assert detail.json()["topic"] == "路由形状快照"

        runs = client.get(f"/api/v1/cases/{case_id}/runs")
        assert runs.status_code == 200
        assert runs.json() == []
