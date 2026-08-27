"""M22: resilience - failure classification, retry, circuit breaker, dead letters."""

from __future__ import annotations

import asyncio
import os
import random
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.application.resilience_service import ResilienceService
from app.core.config import Settings
from app.infrastructure.database.engine import Database
from app.infrastructure.database.resilience_repository import ResilienceRepository
from app.main import create_app
from app.schemas.cases import CreateCaseRequest
from app.services.resilience import (
    STATE_CLOSED,
    STATE_HALF_OPEN,
    STATE_OPEN,
    AdmissionController,
    CircuitBreaker,
    RetryPolicy,
    StuckDetector,
    choose_fallback_route,
    classify_exception,
    kill_switch_active,
)

_DB_ROOT = "E:/Graduate_work_folder/Agent_develop/Project/COIFESP_Agent/Project/backend/data"

# ---------- 错误分类 ----------

def test_classify_timeout_transient_retryable() -> None:
    result = classify_exception(TimeoutError("slow"), scope="model")
    assert result.classification == "transient"
    assert result.retryable is True


def test_classify_429_rate_limited_respects_retry_after() -> None:
    result = classify_exception(
        None, status_code=429, scope="platform", retry_after=13.5
    )
    assert result.classification == "rate_limited"
    assert result.retryable is True
    assert result.retry_after_seconds == 13.5


def test_classify_401_auth_required_not_retryable() -> None:
    result = classify_exception(None, status_code=401, scope="platform")
    assert result.classification == "auth_required"
    assert result.retryable is False


def test_classify_permanent_4xx_not_retryable() -> None:
    result = classify_exception(None, status_code=400, scope="tool")
    assert result.classification == "permanent_input"
    assert result.retryable is False


def test_classify_policy_denied_not_bypassable() -> None:
    result = classify_exception(None, error_code="sandbox_denied", scope="tool")
    assert result.classification == "policy_denied"
    assert result.retryable is False


def test_classify_unknown_limited_retries() -> None:
    result = classify_exception(RuntimeError("odd failure"), scope="media")
    assert result.classification == "unknown"
    assert result.retryable is True  # 有限次数后进入死信


# ---------- RetryPolicy ----------

def test_retry_policy_exponential_backoff_with_cap() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        base_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
        jitter_ratio=0.0,
    )
    assert policy.next_backoff(1) == 1.0
    assert policy.next_backoff(2) == 2.0
    assert policy.next_backoff(3) == 4.0
    assert policy.next_backoff(4) == 8.0
    assert policy.next_backoff(10) == 8.0  # 上限


def test_retry_policy_respects_retry_after() -> None:
    policy = RetryPolicy(max_attempts=3, jitter_ratio=0.0)
    assert policy.next_backoff(1, retry_after=60.0) == 60.0
    assert policy.next_backoff(1, retry_after=0.0) == 0.0


def test_retry_policy_jitter_bounds() -> None:
    policy = RetryPolicy(
        max_attempts=3,
        base_backoff_seconds=10.0,
        max_backoff_seconds=30.0,
        jitter_ratio=0.2,
    )
    rng = random.Random(7)
    for attempt in range(1, 4):
        backoff = policy.next_backoff(attempt, rng=rng)
        exponential = min(10.0 * (2 ** (attempt - 1)), 30.0)
        assert exponential * 0.8 <= backoff <= exponential * 1.2


def test_retry_policy_exhausted_and_budget() -> None:
    policy = RetryPolicy(max_attempts=3, total_time_budget_seconds=10)
    assert not policy.exhausted(2)
    assert policy.exhausted(3)
    assert policy.within_budget(9.9)
    assert not policy.within_budget(10.0)


# ---------- CircuitBreaker（FakeClock） ----------

def _now() -> float:
    return 1_000_000.0


def test_circuit_breaker_full_state_machine() -> None:
    breaker = CircuitBreaker(
        failure_threshold=3,
        min_request_count=2,
        half_open_timeout_seconds=30,
        half_open_success_threshold=2,
    )
    now = _now()
    assert breaker.state == STATE_CLOSED
    # closed -> open（连续失败达阈值）
    breaker.record_failure(now)
    breaker.record_failure(now)
    breaker.record_failure(now)
    assert breaker.state == STATE_OPEN
    # open 期间拒绝请求
    assert breaker.allow_request(now + 10) is False
    # 半开超时后允许一次探测
    assert breaker.allow_request(now + 31) is True
    assert breaker.state == STATE_HALF_OPEN
    # 半开探测失败 -> 回到 open
    breaker.record_failure(now + 31)
    assert breaker.state == STATE_OPEN
    assert breaker.allow_request(now + 40) is False
    # 再次半开并连续成功 -> closed
    assert breaker.allow_request(now + 62) is True
    breaker.record_success(now + 62)
    breaker.record_success(now + 62)
    assert breaker.state == STATE_CLOSED


def test_circuit_breaker_window_failure_rate() -> None:
    breaker = CircuitBreaker(
        failure_threshold=10,
        min_request_count=4,
        window_seconds=60,
    )
    now = _now()
    # 4 个请求 3 失败 1 成功 -> 失败率 75% >= 50% -> open
    breaker.record_success(now)
    breaker.record_failure(now)
    breaker.record_failure(now)
    breaker.record_failure(now)
    assert breaker.state == STATE_OPEN


def test_circuit_breaker_success_closes_and_resets() -> None:
    breaker = CircuitBreaker(failure_threshold=2)
    now = _now()
    breaker.record_failure(now)
    breaker.record_failure(now)
    assert breaker.state == STATE_OPEN
    breaker.reset()
    assert breaker.state == STATE_CLOSED
    assert breaker.failure_count == 0


# ---------- 背压与准入 ----------

def test_admission_queue_full_rejects_with_retry_after() -> None:
    controller = AdmissionController(queue_capacity=10, reserved_slots=4)
    decision = controller.admit(queue_depth=10, estimated_wait_seconds=1.0)
    assert decision.decision == "rejected"
    assert decision.retry_after_seconds == 10.0


def test_admission_db_watermark_defers() -> None:
    controller = AdmissionController(db_watermark=0.9)
    decision = controller.admit(
        queue_depth=0, estimated_wait_seconds=1.0, db_usage=0.95
    )
    assert decision.decision == "deferred"


def test_admission_priority_reserved_slots() -> None:
    controller = AdmissionController(queue_capacity=10, reserved_slots=4)
    # 非优先在深度 6 即被拒（10-4）；优先仍可进入。
    assert (
        controller.admit(queue_depth=6, estimated_wait_seconds=1.0).decision
        == "rejected"
    )
    assert (
        controller.admit(
            queue_depth=6, estimated_wait_seconds=1.0, is_priority=True
        ).decision
        == "admitted"
    )


def test_admission_budget_exhausted_backpressure() -> None:
    controller = AdmissionController(budget_exhausted=True)
    assert (
        controller.admit(queue_depth=0, estimated_wait_seconds=0).decision
        == "rejected"
    )
    assert (
        controller.admit(
            queue_depth=0, estimated_wait_seconds=0, is_priority=True
        ).decision
        == "admitted"
    )


# ---------- Stuck Detector ----------

def test_stuck_detector_heartbeat_alive_never_stuck() -> None:
    detector = StuckDetector(
        heartbeat_stale_seconds=300, stage_max_seconds=3600
    )
    now = datetime.now(UTC)
    # 心跳新鲜：即使任务已运行 10 小时也不算卡死。
    assert (
        detector.is_stuck(
            last_heartbeat_at=now - timedelta(seconds=60),
            stage_started_at=now - timedelta(hours=10),
            stage="crawl",
            process_alive=True,
            now=now,
        )
        is False
    )


def test_stuck_detector_stale_without_process() -> None:
    detector = StuckDetector(
        heartbeat_stale_seconds=300, stage_max_seconds=3600
    )
    now = datetime.now(UTC)
    assert (
        detector.is_stuck(
            last_heartbeat_at=now - timedelta(hours=2),
            stage_started_at=now - timedelta(hours=5),
            stage="media",
            process_alive=False,
            now=now,
        )
        is True
    )


# ---------- 降级路由 ----------

def test_fallback_route_degrades_with_actual_model() -> None:
    decision = choose_fallback_route(
        primary_model="deepseek-reasoning",
        primary_healthy=False,
        fallback_models=["deepseek-flash", "qwen"],
        fallback_health={"deepseek-flash": True, "qwen": False},
    )
    assert decision is not None
    assert decision.degraded is True
    assert decision.actual_model == "deepseek-flash"


def test_fallback_route_none_when_healthy() -> None:
    assert (
        choose_fallback_route(
            primary_model="m1",
            primary_healthy=True,
            fallback_models=["m2"],
            fallback_health={"m2": True},
        )
        is None
    )


def test_fallback_route_no_compatible_candidate() -> None:
    decision = choose_fallback_route(
        primary_model="m1",
        primary_healthy=False,
        fallback_models=["m2"],
        fallback_health={"m2": True},
        capabilities_compatible=False,
    )
    assert decision is not None
    assert decision.actual_model == ""
    assert decision.capability_drop == ["llm"]


# ---------- Kill Switch 层级 ----------

def test_kill_switch_global_overrides_lower_levels() -> None:
    switches = [
        {"scope": "global", "target": "*", "status": "on"},
        {"scope": "tool", "target": "collect_social_posts", "status": "off"},
    ]
    killed, source = kill_switch_active(
        switches, scope="tool", target="collect_social_posts"
    )
    assert killed is True
    assert source == "kill_switch:global:*"


def test_kill_switch_low_level_off_cannot_bypass_high_level() -> None:
    switches = [
        {"scope": "dependency", "target": "douyin", "status": "on"},
        {"scope": "tool", "target": "*", "status": "off"},
    ]
    killed, _ = kill_switch_active(switches, scope="tool", target="crawl")
    assert killed is True


def test_kill_switch_target_specific() -> None:
    switches = [{"scope": "platform", "target": "douyin", "status": "on"}]
    assert kill_switch_active(switches, scope="platform", target="douyin")[0]
    assert not kill_switch_active(switches, scope="platform", target="weibo")[0]


# ---------- 仓储集成 ----------

def _db_url(name: str) -> str:
    return "sqlite+aiosqlite:///" + _DB_ROOT.replace("\\", "/") + "/" + name


def _cleanup_db(name: str) -> None:
    path = os.path.join(_DB_ROOT, name)
    try:
        os.remove(path)
    except OSError:
        pass



def test_resilience_persistence_integration() -> None:
    """一个 database 覆盖：死信/事故/开关/熔断持久化/重试链（单次建库）。"""
    _cleanup_db("resilience_integration.db")
    database = Database(_db_url("resilience_integration.db"))

    async def run() -> None:
        await database.create_schema()
        repo = ResilienceRepository(database)
        # ---- 死信：同 key 更新而非重复 ----
        item = await repo.enqueue_dead_letter(
            operation_key="op:1",
            dependency="douyin",
            scope="platform",
            classification="unknown",
            error_code="boom",
            attempts=3,
            payload_hash="abc123",
            policy_version="1.0",
            code_version="0.1.0",
            recovery_hint="manual review",
        )
        assert item.status == "pending"
        again = await repo.enqueue_dead_letter(
            operation_key="op:1",
            dependency="douyin",
            scope="platform",
            classification="transient",
            error_code="retry",
            attempts=5,
            payload_hash="abc123",
            policy_version="1.0",
            code_version="0.1.0",
            recovery_hint="retry",
        )
        assert again.id == item.id and again.attempts == 5
        updated = await repo.update_dead_letter(item.id, status="resolved")
        assert updated is not None and updated.status == "resolved"
        # ---- 事故生命周期 ----
        incident = await repo.create_incident(
            title="douyin outage", severity="critical", impact="crawls failing"
        )
        closed = await repo.close_incident(
            incident.id, recovery={"restored": True}, retro={"notes": "ok"}
        )
        assert closed is not None and closed.status == "closed"
        assert closed.recovery_json == {"restored": True}
        # ---- Kill Switch 层级 ----
        switch = await repo.create_kill_switch(
            scope="global", target="*", reason="incident", actor="ops"
        )
        killed, _ = await repo.is_killed("tool", "crawl")
        assert killed is True
        await repo.disable_kill_switch(switch.id, actor="ops", reason="recovered")
        killed_after, _ = await repo.is_killed("tool", "crawl")
        assert killed_after is False
        # ---- 熔断状态持久化（跨加载） ----
        breaker = CircuitBreaker(failure_threshold=2)
        breaker.record_failure(1.0)
        breaker.record_failure(1.0)
        await repo.save_breaker_state(
            dependency="douyin", scope="platform", breaker=breaker
        )
        record = await repo.get_breaker_state("douyin", "platform")
        assert record is not None and record.state == STATE_OPEN
        # ---- 重试链：有限重试后死信 ----
        settings = Settings(database_url=_db_url("resilience_integration.db"))
        service = ResilienceService(repo, settings, telemetry=None)
        classification = classify_exception(TimeoutError("x"), scope="model")
        retry, backoff, status = await service.should_retry(
            operation_key="llm:1",
            dependency="llm",
            scope="model",
            classification=classification,
            payload_hash="h",
            first_error="timeout",
        )
        assert retry is True and backoff >= 0 and status == "pending"
        for _ in range(3):
            final = await service.should_retry(
                operation_key="llm:1",
                dependency="llm",
                scope="model",
                classification=classification,
                payload_hash="h",
                first_error="timeout",
            )
        assert final[0] is False and final[2] == "dead_lettered"

    async def _main() -> None:
        await run()
        await database.dispose()

    asyncio.run(_main())
    _cleanup_db("resilience_integration.db")


def test_resilience_api_health_and_kill_switch_approval() -> None:
    _cleanup_db("resilience_api.db")
    app = create_app(
        Settings(
            database_url=_db_url("resilience_api.db"),
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        health = client.get("/api/v1/system/resilience/health")
        assert health.status_code == 200
        assert "dependencies" in health.json()
        circuits = client.get("/api/v1/system/resilience/circuits")
        assert circuits.status_code == 200
        # Kill Switch 开启需要 M21 审批（approval_id）
        denied = client.post(
            "/api/v1/system/resilience/kill-switches",
            json={"scope": "tool", "target": "crawl", "reason": "test"},
        )
        assert denied.status_code == 409
        # 任意非空 ID 不能绕过审批。
        fake = client.post(
            "/api/v1/system/resilience/kill-switches",
            json={
                "scope": "tool",
                "target": "crawl",
                "reason": "test",
                "approval_id": "appr-1",
            },
        )
        assert fake.status_code == 409

        async def seed_approval() -> str:
            container = app.state.container
            case = await container.repository.create_case(
                CreateCaseRequest(
                    title="resilience approval",
                    topic="test",
                    platforms=["weibo"],
                )
            )
            run = await container.repository.create_agent_run(
                case_id=case.id,
                turn_id=None,
                objective="enable kill switch",
            )
            approval = await container.repository.create_approval(
                run_id=run.id,
                action="kill_switch",
                reason="incident response",
                request_payload={},
            )
            await container.repository.update_approval_full(
                approval.id,
                status="approved",
                decision="approve",
                actor="operator",
            )
            return approval.id

        assert client.portal is not None
        approval_id = client.portal.call(seed_approval)
        enabled = client.post(
            "/api/v1/system/resilience/kill-switches",
            json={
                "scope": "tool",
                "target": "crawl",
                "reason": "test",
                "approval_id": approval_id,
            },
        )
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "on"        # 事故生命周期
        incident = client.post(
            "/api/v1/system/resilience/incidents",
            json={"title": "演练", "severity": "warning"},
        )
        assert incident.status_code == 200
        incident_id = incident.json()["id"]
        closed = client.post(
            f"/api/v1/system/resilience/incidents/{incident_id}:close",
            json={"recovery": {"ok": True}},
        )
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"


