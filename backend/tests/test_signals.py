"""M6: Global Signals（Alert adapter）与 Workspace Overview 测试。

service 层：alert → signal 映射、状态动作委托既有 alert 状态机、severity 排序。
C4：Signal 与 Monitor 共用同一 alert 状态机，非法逆向转换被拒绝且语义一致。
API 层：/signals 过滤参数、未知 signal 404、/workspace/overview 聚合。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.signal_service import SignalService
from app.core.config import Settings
from app.core.errors import ApplicationError
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


async def test_signal_state_machine_rejects_illegal_transitions(tmp_path: Path) -> None:
    """C4：Signal 遵循同一 alert 状态机 —— 逆向/旁路转换被拒绝。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 's4.db'}")
    service = await _seed_signal(database)
    signal = (await service.list_signals())[0]
    signal_id = signal.id

    await service.change_status(signal_id, "acknowledge")
    resolved = await service.change_status(signal_id, "resolve")
    assert resolved.status == "resolved"

    # resolved -> acknowledged 非法（旧 Monitor 状态机不允许的逆向转换）
    with pytest.raises(ApplicationError) as exc:
        await service.change_status(signal_id, "acknowledge")
    assert exc.value.code == "alert_status_transition_invalid"

    # suppressed 可从 resolved 进入（合法正向），但 suppressed -> resolve 非法
    suppressed = await service.change_status(signal_id, "suppress")
    assert suppressed.status == "suppressed"
    with pytest.raises(ApplicationError) as exc:
        await service.change_status(signal_id, "resolve")
    assert exc.value.code == "alert_status_transition_invalid"


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


def test_signal_and_monitor_api_share_state_machine(tmp_path: Path) -> None:
    """C4：Signal API 与 Monitor API 对合法/非法转换语义完全一致。"""
    import asyncio

    db_path = tmp_path / "s5.db"
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    asyncio.run(database.create_schema())
    repo = MonitorRepository(database)
    from app.application.repositories import ApplicationRepository

    app_repo = ApplicationRepository(database)

    async def seed() -> tuple[str, str]:
        case = await app_repo.create_case(
            CreateCaseRequest(topic="共用状态机", platforms=["weibo"])
        )
        monitor = await repo.create_monitor(case_id=case.id, name="m", interval_seconds=3600)
        rule = await repo.create_rule(monitor_id=monitor.id, rule_type="absolute_volume")
        alert, _ = await repo.upsert_alert_occurrence(
            monitor_id=monitor.id,
            rule_id=rule.id,
            fingerprint="f",
            cooldown_bucket="all",
            severity="warning",
            explanation="e",
            metric_snapshot={},
            evidence_refs={},
        )
        return case.id, alert.id

    case_id, alert_id = asyncio.run(seed())
    asyncio.run(database.dispose())

    app = create_app(Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True))
    with TestClient(app) as client:
        # Signal API acknowledge → Monitor API resolve（跨 API 正向路径）
        ack = client.post(f"/api/v1/signals/{alert_id}:acknowledge", json={})
        assert ack.status_code == 200 and ack.json()["status"] == "acknowledged"
        resolved = client.post(
            f"/api/v1/cases/{case_id}/alerts/{alert_id}:resolve", json={}
        )
        assert resolved.status_code == 200

        # Signal API resolved -> acknowledge 非法，错误码与 Monitor 状态机一致
        illegal = client.post(f"/api/v1/signals/{alert_id}:acknowledge", json={})
        assert illegal.status_code == 400
        assert illegal.json()["code"] == "alert_status_transition_invalid"

        # Monitor API 同一非法转换返回相同错误码
        monitor_illegal = client.post(
            f"/api/v1/cases/{case_id}/alerts/{alert_id}:acknowledge", json={}
        )
        assert monitor_illegal.status_code == 400
        assert monitor_illegal.json()["code"] == "alert_status_transition_invalid"


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
