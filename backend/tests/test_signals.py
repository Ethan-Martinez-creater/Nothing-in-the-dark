"""M6: Global Signals（Alert adapter）与 Workspace Overview 测试。

service 层：alert → signal 映射、状态动作委托既有 alert 状态机、severity 排序。
API 层：/signals 过滤参数、未知 signal 404、/workspace/overview 聚合。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.application.signal_service import SignalService
from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.database.monitor_repository import MonitorRepository
from app.main import create_app
from app.schemas.cases import CreateCaseRequest


async def _seed_signal(database: Database) -> SignalService:
    await database.create_schema()
    from app.application.repositories import ApplicationRepository

    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="信号案例", platforms=["weibo"])
    )
    monitors = MonitorRepository(database)
    service = SignalService(database, monitors)
    monitor = await monitors.create_monitor(
        case_id=case.id,
        name="音量监测",
        platforms=["weibo"],
    )
    rule = await monitors.create_rule(
        monitor_id=monitor.id,
        rule_type="absolute_volume",
        parameters={"threshold": 100},
        severity="critical",
    )
    await monitors.upsert_alert_occurrence(
        monitor_id=monitor.id,
        rule_id=rule.id,
        fingerprint="fp-1",
        cooldown_bucket="b1",
        severity="critical",
        explanation="讨论量突破阈值 120 > 100",
        metric_snapshot={"volume": 120, "confidence": 0.9},
        evidence_refs={"post_ids": ["p-1"]},
    )
    return service


async def test_alert_maps_to_signal(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 's1.db'}")
    service = await _seed_signal(database)

    signals = await service.list_signals()
    assert len(signals) == 1
    signal = signals[0]
    assert signal.source_type == "monitor_alert"
    assert signal.signal_type == "volume_spike"
    assert signal.severity == "critical"
    assert signal.status == "open"
    assert signal.title == "讨论量达到告警阈值"
    assert "120" in signal.why_it_matters
    assert signal.evidence_refs.get("post_ids") == ["p-1"]
    assert signal.confidence == 0.9
    assert signal.case_title == "信号案例"

    # 状态动作委托既有 alert 状态机
    acknowledged = await service.change_status(signal.id, "acknowledge")
    assert acknowledged.status == "acknowledged"
    resolved = await service.change_status(signal.id, "resolve")
    assert resolved.status == "resolved"

    # 过滤：默认视图（open+acknowledged）不含 resolved
    default_view = await service.list_signals(statuses=["open", "acknowledged"])
    assert default_view == []
    only_resolved = await service.list_signals(statuses=["resolved"])
    assert len(only_resolved) == 1


def test_signals_api_filters_and_404(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 's2.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        listed = client.get("/api/v1/signals")
        assert listed.status_code == 200
        assert listed.json() == []

        filtered = client.get(
            "/api/v1/signals",
            params={"status": "open,acknowledged", "severity": "critical"},
        )
        assert filtered.status_code == 200

        missing = client.get("/api/v1/signals/does-not-exist")
        assert missing.status_code == 404
        assert missing.json()["code"] == "signal_not_found"


def test_workspace_overview_aggregate(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 's3.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        client.post("/api/v1/cases", json={"topic": "调查A", "platforms": ["weibo"]})
        client.post("/api/v1/cases", json={"topic": "调查B", "platforms": ["weibo"]})

        overview = client.get("/api/v1/workspace/overview")
        assert overview.status_code == 200
        body = overview.json()
        assert body["counts"]["investigations"] == 2
        assert body["counts"]["open_signals"] == 0
        assert body["counts"]["pending_approvals"] == 0
        assert body["counts"]["running_runs"] == 0
        assert len(body["recent_investigations"]) == 2
        assert body["top_signals"] == []
        assert body["recent_reports"] == []
