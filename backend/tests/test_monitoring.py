"""Tests for continuous monitoring & alerting (01)."""

from __future__ import annotations

import asyncio
import atexit
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.monitor_scheduler import MonitorScheduler
from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.core.errors import ResourceNotFoundError
from app.infrastructure.database import Database
from app.infrastructure.database.monitor_repository import MonitorRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.main import create_app
from app.schemas.cases import CreateCaseRequest
from app.services import monitoring

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    """Per-test DB file under the workspace root.

    The backend directory has a broken NTFS ACL (mkdir/rmdir is denied for the
    Python child process) and the DSH sandbox temp dir cannot host SQLite
    files, so tests place their SQLite DB under the workspace root instead.
    """
    d = _WORKSPACE_ROOT / f"coifesp-monitor-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


def _settings(db_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}",
        demo_mode=True,
        monitor_scheduler_enabled=False,
    )


# ---------- cron 解析（单元） ---------------------------------------------


def test_parse_cron_fields() -> None:
    assert 0 in monitoring.parse_cron("0 9 * * *")[0]
    assert 9 in monitoring.parse_cron("0 9 * * *")[1]
    assert monitoring.parse_cron("*/15 * * * *")[0] == set(range(0, 60, 15))
    assert monitoring.parse_cron("0 9,18 * * *")[1] == {9, 18}
    assert monitoring.parse_cron("0 9-11 * * *")[1] == {9, 10, 11}


def test_parse_cron_invalid() -> None:
    with pytest.raises(ValueError):
        monitoring.parse_cron("* * * *")
    with pytest.raises(ValueError):
        monitoring.parse_cron("61 * * * *")
    with pytest.raises(ValueError):
        monitoring.parse_cron("* 25 * * *")


def test_cron_next_shanghai_timezone() -> None:
    after = datetime(2026, 8, 20, 1, 0, 0)
    nxt = monitoring.cron_next("0 9 * * *", after=after, tz_name="Asia/Shanghai")
    assert nxt == datetime(2026, 8, 20, 1, 0, 0, tzinfo=UTC)


def test_cron_next_end_of_month() -> None:
    after = datetime(2026, 1, 31, 10, 0, 0)
    nxt = monitoring.cron_next("0 9 * * *", after=after, tz_name="Asia/Shanghai")
    assert nxt.month == 2


# ---------- 时间窗规划（单元） --------------------------------------------


def test_compute_window_first_run_uses_lookback() -> None:
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    start, end, is_first = monitoring.compute_window(
        schedule_type="interval",
        interval_seconds=3600,
        cron=None,
        timezone="Asia/Shanghai",
        lookback_seconds=7200,
        last_window_end=None,
        now=now,
    )
    assert is_first is True
    assert end == now
    assert start == now - timedelta(seconds=7200)


def test_compute_window_incremental_advances() -> None:
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    last = datetime(2026, 8, 20, 11, 0, 0, tzinfo=UTC)
    start, end, is_first = monitoring.compute_window(
        schedule_type="interval",
        interval_seconds=3600,
        cron=None,
        timezone="Asia/Shanghai",
        lookback_seconds=7200,
        last_window_end=last,
        now=now,
    )
    assert is_first is False
    assert start == last
    assert end == now


def test_compute_window_overlap() -> None:
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    last = datetime(2026, 8, 20, 11, 0, 0, tzinfo=UTC)
    start, _end, _first = monitoring.compute_window(
        schedule_type="interval",
        interval_seconds=3600,
        cron=None,
        timezone="Asia/Shanghai",
        lookback_seconds=7200,
        last_window_end=last,
        now=now,
        overlap_seconds=60,
    )
    assert start == last - timedelta(seconds=60)


def test_compute_next_scheduled_at_interval() -> None:
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    first = monitoring.compute_next_scheduled_at(
        schedule_type="interval",
        interval_seconds=3600,
        cron=None,
        timezone="Asia/Shanghai",
        last_scheduled_at=None,
        now=now,
    )
    assert first == now
    nxt = monitoring.compute_next_scheduled_at(
        schedule_type="interval",
        interval_seconds=3600,
        cron=None,
        timezone="Asia/Shanghai",
        last_scheduled_at=now,
        now=now + timedelta(seconds=10),
    )
    assert nxt == now + timedelta(seconds=3600)
    caught_up = monitoring.compute_next_scheduled_at(
        schedule_type="interval",
        interval_seconds=3600,
        cron=None,
        timezone="Asia/Shanghai",
        last_scheduled_at=now,
        now=now + timedelta(seconds=10 * 3600),
    )
    assert caught_up >= now + timedelta(seconds=10 * 3600)


# ---------- 告警规则（单元） ----------------------------------------------


def _rule_window(**overrides: object) -> dict:
    window: dict[str, object] = {
        "post_count": 10,
        "comment_count": 5,
        "engagement_total": 100,
        "accounts": [],
        "_window": {"start": None, "end": None},
    }
    window.update(overrides)
    return window


def test_alert_absolute_volume() -> None:
    hit = monitoring.evaluate_rule(
        rule_type="absolute_volume",
        parameters={"metric": "post_count", "threshold": 10},
        severity="warning",
        window=_rule_window(post_count=12),
        baseline=None,
        account_watchlist=[],
        narratives=[],
    )
    assert hit is not None and hit.rule_type == "absolute_volume"
    assert (
        monitoring.evaluate_rule(
            rule_type="absolute_volume",
            parameters={"metric": "post_count", "threshold": 10},
            severity="warning",
            window=_rule_window(post_count=9),
            baseline=None,
            account_watchlist=[],
            narratives=[],
        )
        is None
    )


def test_alert_rate_growth_skips_small_baseline() -> None:
    hit = monitoring.evaluate_rule(
        rule_type="rate_growth",
        parameters={"metric": "post_count", "min_growth_ratio": 2.0, "min_baseline": 5},
        severity="warning",
        window=_rule_window(post_count=100),
        baseline={"post_count": 3},
        account_watchlist=[],
        narratives=[],
    )
    assert hit is None
    hit = monitoring.evaluate_rule(
        rule_type="rate_growth",
        parameters={"metric": "post_count", "min_growth_ratio": 2.0, "min_baseline": 5},
        severity="warning",
        window=_rule_window(post_count=20),
        baseline={"post_count": 5},
        account_watchlist=[],
        narratives=[],
    )
    assert hit is not None and hit.rule_type == "rate_growth"


def test_alert_anomaly_insufficient_baseline() -> None:
    hit = monitoring.evaluate_rule(
        rule_type="anomaly",
        parameters={"metric": "post_count", "min_samples": 5},
        severity="warning",
        window=_rule_window(post_count=100),
        baseline={"history": {"post_count": [1, 2]}},
        account_watchlist=[],
        narratives=[],
    )
    # 样本不足不制造告警。
    assert hit is None
    # 诊断状态通过 anomaly_baseline_status 查询。
    assert (
        monitoring.anomaly_baseline_status(
            {"history": {"post_count": [1, 2]}},
            metric="post_count",
            min_samples=5,
        )
        == "insufficient"
    )


def test_alert_anomaly_detects_outlier() -> None:
    hit = monitoring.evaluate_rule(
        rule_type="anomaly",
        parameters={"metric": "post_count", "min_samples": 5, "mad_threshold": 3.0},
        severity="warning",
        window=_rule_window(post_count=500),
        baseline={"history": {"post_count": [10, 11, 10, 9, 10, 11]}},
        account_watchlist=[],
        narratives=[],
    )
    assert hit is not None
    assert hit.metric_snapshot["z_score"] > 3.0


def test_alert_key_account_matches_watchlist() -> None:
    watchlist = [{"name": "官方发布", "normalized_name": "官方发布", "platform": "weibo"}]
    hit = monitoring.evaluate_rule(
        rule_type="key_account",
        parameters={},
        severity="critical",
        window=_rule_window(accounts=[{"name": "官方发布", "platform": "weibo", "id": "u1"}]),
        baseline=None,
        account_watchlist=watchlist,
        narratives=[],
    )
    assert hit is not None and hit.rule_type == "key_account"
    hit2 = monitoring.evaluate_rule(
        rule_type="key_account",
        parameters={},
        severity="critical",
        window=_rule_window(accounts=[{"name": "普通用户", "platform": "weibo", "id": "u2"}]),
        baseline=None,
        account_watchlist=watchlist,
        narratives=[],
    )
    assert hit2 is None


def test_alert_narrative_min_sample() -> None:
    hit = monitoring.evaluate_rule(
        rule_type="narrative",
        parameters={"min_sample": 3},
        severity="info",
        window={},
        baseline=None,
        account_watchlist=[],
        narratives=[{"label": "新叙事", "count": 5}],
    )
    assert hit is not None and hit.rule_type == "narrative"
    hit2 = monitoring.evaluate_rule(
        rule_type="narrative",
        parameters={"min_sample": 3},
        severity="info",
        window={},
        baseline=None,
        account_watchlist=[],
        narratives=[{"label": "弱叙事", "count": 1}],
    )
    assert hit2 is None


# ---------- 仓储 ----------------------------------------------------------


async def _setup_repo(db_path: Path) -> tuple[MonitorRepository, str]:
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    from app.application.repositories import ApplicationRepository

    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="监测测试", platforms=["weibo"]))
    return MonitorRepository(database), case.id


async def test_monitor_crud_and_optimistic_lock() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    monitor = await repo.create_monitor(
        case_id=case_id,
        name="每日监测",
        interval_seconds=3600,
        platforms=["weibo"],
    )
    assert monitor.version == 1
    listed = await repo.list_monitors(case_id=case_id)
    assert len(listed) == 1

    updated = await repo.update_monitor(monitor.id, version=1, name="每日监测(改)")
    assert updated.version == 2 and updated.name == "每日监测(改)"

    with pytest.raises(ResourceNotFoundError):
        await repo.update_monitor(monitor.id, version=1, name="冲突")

    await repo.set_monitor_enabled(monitor.id, False)
    assert (await repo.get_monitor(monitor.id)).enabled is False


async def test_execution_idempotent() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    monitor = await repo.create_monitor(case_id=case_id, name="m", interval_seconds=3600)
    scheduled = datetime(2026, 8, 20, 0, 0, 0, tzinfo=UTC)
    first = await repo.create_execution(monitor_id=monitor.id, scheduled_at=scheduled)
    assert first is not None
    second = await repo.create_execution(monitor_id=monitor.id, scheduled_at=scheduled)
    assert second is None


async def test_alert_occurrence_merges() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    monitor = await repo.create_monitor(case_id=case_id, name="m", interval_seconds=3600)
    rule = await repo.create_rule(monitor_id=monitor.id, rule_type="absolute_volume")
    record, created = await repo.upsert_alert_occurrence(
        monitor_id=monitor.id,
        rule_id=rule.id,
        fingerprint="fp1",
        cooldown_bucket="all",
        severity="warning",
        explanation="e",
        metric_snapshot={},
        evidence_refs={},
    )
    assert created is True and record.trigger_count == 1
    merged, created2 = await repo.upsert_alert_occurrence(
        monitor_id=monitor.id,
        rule_id=rule.id,
        fingerprint="fp1",
        cooldown_bucket="all",
        severity="warning",
        explanation="e",
        metric_snapshot={},
        evidence_refs={},
    )
    assert created2 is False and merged.trigger_count == 2


async def test_cursor_upsert() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    monitor = await repo.create_monitor(case_id=case_id, name="m", interval_seconds=3600)
    end = datetime(2026, 8, 20, 1, 0, 0, tzinfo=UTC)
    await repo.upsert_cursor(monitor_id=monitor.id, platform="weibo", last_window_end=end)
    cursor = await repo.get_cursor(monitor.id, "weibo")
    assert cursor is not None
    # SQLite 读回 DateTime 丢失 tzinfo，用 to_utc 归一后比较。
    assert monitoring.to_utc(cursor.last_window_end) == end
    await repo.record_cursor_failure(monitor.id, "weibo")
    cursor = await repo.get_cursor(monitor.id, "weibo")
    assert cursor is not None and cursor.consecutive_failures == 1


# ---------- Scheduler 集成（Fake crawler） --------------------------------


class _FakeCrawler:
    def __init__(self, posts: list[dict[str, object]]) -> None:
        self._posts = posts
        self.calls = 0

    async def collect(self, request: object) -> list[dict[str, object]]:
        self.calls += 1
        return self._posts


async def _build_scheduler(db_path: Path, posts: list[dict[str, object]]):
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    from app.application.repositories import ApplicationRepository

    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="监测调度", platforms=["weibo"]))
    repo = MonitorRepository(database)
    social = SocialRepository(database)
    monitor = await repo.create_monitor(
        case_id=case.id,
        name="监测",
        interval_seconds=3600,
        platforms=["weibo"],
        lookback_seconds=3600,
    )
    await repo.create_rule(
        monitor_id=monitor.id,
        rule_type="absolute_volume",
        parameters={"metric": "post_count", "threshold": 1},
    )
    crawler = _FakeCrawler(posts)
    scheduler = MonitorScheduler(repo, social, crawler, agent_service=None, enabled=False)
    return scheduler, repo, monitor, case


async def test_scheduler_run_now_persists_and_alerts() -> None:
    scheduler, repo, monitor, case = await _build_scheduler(
        _tmp_db(),
        [
            {
                "platform": "weibo",
                "native_id": "w1",
                "content": "监测内容",
                "engagement": 5,
                "author": "用户A",
                "published_at": "2026-08-20T00:00:00+00:00",
            }
        ],
    )
    execution = await scheduler.run_now(monitor.id)
    assert execution.status == "scheduled"  # run-now 只创建，由 Worker 领取执行

    # 幂等：同一 key 返回同一 execution。
    first = await scheduler.run_now(monitor.id, idempotency_key="k1")
    second = await scheduler.run_now(monitor.id, idempotency_key="k1")
    assert first.id == second.id

    # Worker 领取并执行。
    final = execution
    for _ in range(30):
        await scheduler.tick()
        await asyncio.sleep(0.01)
        final = await repo.get_execution(execution.id)
        if final.status in ("succeeded", "partial", "failed"):
            break

    assert final.status == "succeeded"
    assert final.platform_stats["totals"]["post_count"] == 1
    assert final.platform_stats["alerts_fired"] == 1
    cursor = await repo.get_cursor(monitor.id, "weibo")
    assert cursor is not None and cursor.last_window_end is not None
    alerts = await repo.list_alerts(case_id=case.id)
    assert len(alerts) == 1
    assert alerts[0].status == "open"


async def test_scheduler_claims_uniquely() -> None:
    database = Database(f"sqlite+aiosqlite:///{_tmp_db()}")
    await database.create_schema()
    from app.application.repositories import ApplicationRepository

    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="并发领取", platforms=["weibo"]))
    repo = MonitorRepository(database)
    monitor = await repo.create_monitor(case_id=case.id, name="m", interval_seconds=3600)
    scheduled = datetime(2026, 8, 20, 0, 0, 0, tzinfo=UTC)
    await repo.create_execution(monitor_id=monitor.id, scheduled_at=scheduled)
    first = await repo.claim_execution("w1", 600)
    second = await repo.claim_execution("w2", 600)
    assert first is not None
    if second is not None:
        assert second.id != first.id
    await database.dispose()


# ---------- API -----------------------------------------------------------


def test_api_monitor_lifecycle() -> None:
    app = create_app(_settings(_tmp_db()))
    with TestClient(app) as client:
        case_id = client.post(
            "/api/v1/cases",
            json={"topic": "监测 API", "platforms": ["weibo"]},
        ).json()["id"]

        created = client.post(
            f"/api/v1/cases/{case_id}/monitors",
            json={
                "name": "监测",
                "schedule_type": "interval",
                "interval_seconds": 3600,
                "platforms": ["weibo"],
            },
        )
        assert created.status_code == 201, created.text
        monitor = created.json()
        assert monitor["enabled"] is True and monitor["version"] == 1

        listed = client.get(f"/api/v1/cases/{case_id}/monitors")
        assert listed.status_code == 200 and len(listed.json()) == 1

        # 乐观锁：先成功更新（version 1 -> 2），再用旧 version 1 更新应冲突。
        first_update = client.patch(
            f"/api/v1/cases/{case_id}/monitors/{monitor['id']}",
            json={"version": 1, "name": "第一次更新"},
        )
        assert first_update.status_code == 200
        assert first_update.json()["version"] == 2
        conflict = client.patch(
            f"/api/v1/cases/{case_id}/monitors/{monitor['id']}",
            json={"version": 1, "name": "冲突"},
        )
        assert conflict.status_code == 404

        paused = client.post(f"/api/v1/cases/{case_id}/monitors/{monitor['id']}:pause")
        assert paused.json()["enabled"] is False
        resumed = client.post(f"/api/v1/cases/{case_id}/monitors/{monitor['id']}:resume")
        assert resumed.json()["enabled"] is True


def test_api_invalid_cron_rejected() -> None:
    app = create_app(_settings(_tmp_db()))
    with TestClient(app) as client:
        case_id = client.post(
            "/api/v1/cases",
            json={"topic": "监测 API", "platforms": ["weibo"]},
        ).json()["id"]
        bad = client.post(
            f"/api/v1/cases/{case_id}/monitors",
            json={"name": "cron", "schedule_type": "cron", "cron": "bad"},
        )
        assert bad.status_code == 400


def test_api_alert_status_machine() -> None:
    db_path = _tmp_db()
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    asyncio.run(database.create_schema())
    repo = MonitorRepository(database)
    from app.application.repositories import ApplicationRepository

    app_repo = ApplicationRepository(database)

    async def seed() -> tuple[str, str]:
        case = await app_repo.create_case(
            CreateCaseRequest(topic="告警状态机", platforms=["weibo"])
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

    app = create_app(_settings(db_path))
    with TestClient(app) as client:
        ack = client.post(
            f"/api/v1/cases/{case_id}/alerts/{alert_id}:acknowledge",
            json={"by": "reviewer"},
        )
        assert ack.status_code == 200 and ack.json()["status"] == "acknowledged"
        again = client.post(
            f"/api/v1/cases/{case_id}/alerts/{alert_id}:acknowledge",
            json={"by": "reviewer"},
        )
        assert again.status_code == 400
        resolved = client.post(f"/api/v1/cases/{case_id}/alerts/{alert_id}:resolve", json={})
        assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"


def test_watchlist_terms_are_added_only_to_matching_platform() -> None:
    watchlist = [
        {"platform": "weibo", "name": "目标账号", "native_id": "123"},
        {"platform": "bilibili", "name": "B站账号"},
    ]
    terms = MonitorScheduler._platform_keywords(["主题"], watchlist, "weibo")
    assert terms == ["主题", "目标账号", "123"]


async def test_monitor_finish_rejects_stale_owner() -> None:
    database = Database(f"sqlite+aiosqlite:///{_tmp_db()}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="监测租约", platforms=["weibo"]))
    repo = MonitorRepository(database)
    monitor = await repo.create_monitor(
        case_id=case.id,
        name="租约",
        schedule_type="interval",
        interval_seconds=60,
        platforms=["weibo"],
    )
    execution = await repo.create_execution(
        monitor_id=monitor.id,
        scheduled_at=datetime.now(UTC),
    )
    assert execution is not None
    claimed = await repo.claim_execution("worker-a", 60)
    assert claimed is not None
    await repo.update_execution(claimed.id, lease_owner="worker-b")
    assert not await repo.finish_execution(claimed.id, "worker-a", status="succeeded")
    current = await repo.get_execution(claimed.id)
    assert current.status == "running"


async def test_lease_loss_does_not_advance_or_mark_platform_cursor() -> None:
    scheduler, repo, monitor, _case = await _build_scheduler(_tmp_db(), [])

    class LeaseLosingCrawler:
        async def collect(self, request: object) -> list[dict[str, object]]:
            request.cancel_event.set()
            return []

    scheduler._crawler = LeaseLosingCrawler()
    execution = await repo.create_execution(monitor_id=monitor.id, scheduled_at=datetime.now(UTC))
    assert execution is not None
    claimed = await repo.claim_execution("local-monitor-worker", 600)
    assert claimed is not None
    with pytest.raises(RuntimeError, match="monitor_execution_lease_lost"):
        await scheduler._execute(claimed.id)
    assert await repo.get_cursor(monitor.id, "weibo") is None
