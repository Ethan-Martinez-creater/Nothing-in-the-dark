"""M22: resilience persistence - health, breakers, retries, dead letters, incidents.

故障隔离与事故处置的持久化层：dependency_health / circuit_breaker_states /
retry_attempts / dead_letter_items / incident_records / kill_switches。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select

from app.core.errors import ApplicationError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    CircuitBreakerStateRecord,
    DeadLetterItemRecord,
    DependencyHealthRecord,
    IncidentRecord,
    KillSwitchRecord,
    RetryAttemptRecord,
)
from app.services.resilience import CircuitBreaker, kill_switch_active


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


class ResilienceRepository:
    """韧性状态持久化：健康矩阵、熔断、重试链、死信、事故与开关。"""

    def __init__(self, database: Database) -> None:
        self._database = database

    # ---- dependency health ----

    async def upsert_dependency_health(
        self,
        *,
        dependency: str,
        scope: str,
        status: str,
        error_code: str = "",
        circuit_state: str = "closed",
        consecutive_failures: int = 0,
        last_success_at: datetime | None = None,
        last_failure_at: datetime | None = None,
    ) -> DependencyHealthRecord:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(DependencyHealthRecord).where(
                    DependencyHealthRecord.dependency == dependency,
                    DependencyHealthRecord.scope == scope,
                )
            )
            if record is None:
                record = DependencyHealthRecord(
                    dependency=dependency,
                    scope=scope,
                    status=status,
                    error_code=error_code,
                    circuit_state=circuit_state,
                    consecutive_failures=consecutive_failures,
                    last_success_at=last_success_at,
                    last_failure_at=last_failure_at,
                )
                session.add(record)
            else:
                record.status = status
                record.error_code = error_code
                record.circuit_state = circuit_state
                record.consecutive_failures = consecutive_failures
                if last_success_at is not None:
                    record.last_success_at = last_success_at
                if last_failure_at is not None:
                    record.last_failure_at = last_failure_at
            await session.commit()
            await session.refresh(record)
            return record

    async def list_dependency_health(self) -> Sequence[DependencyHealthRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(DependencyHealthRecord).order_by(
                    DependencyHealthRecord.dependency,
                    DependencyHealthRecord.scope,
                )
            )
            return result.all()

    # ---- circuit breakers ----

    async def get_breaker_state(
        self, dependency: str, scope: str
    ) -> CircuitBreakerStateRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(CircuitBreakerStateRecord).where(
                    CircuitBreakerStateRecord.dependency == dependency,
                    CircuitBreakerStateRecord.scope == scope,
                )
            )

    async def save_breaker_state(
        self,
        *,
        dependency: str,
        scope: str,
        breaker: CircuitBreaker,
        now: datetime | None = None,
    ) -> CircuitBreakerStateRecord:
        now = _aware(now) or _now()
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(CircuitBreakerStateRecord).where(
                    CircuitBreakerStateRecord.dependency == dependency,
                    CircuitBreakerStateRecord.scope == scope,
                )
            )
            data = breaker.to_dict()
            if record is None:
                record = CircuitBreakerStateRecord(
                    dependency=dependency,
                    scope=scope,
                    state=breaker.state,
                    failure_count=breaker.failure_count,
                    success_count=breaker.success_count,
                    config_version=breaker.config_version,
                    window_started_at=(
                        datetime.fromtimestamp(
                            breaker.window_started_at, tz=UTC
                        )
                        if breaker.window_started_at
                        else None
                    ),
                    opened_at=(
                        datetime.fromtimestamp(breaker.opened_at, tz=UTC)
                        if breaker.opened_at is not None
                        else None
                    ),
                    half_open_probe_at=(
                        datetime.fromtimestamp(breaker.half_open_probe_at, tz=UTC)
                        if breaker.half_open_probe_at is not None
                        else None
                    ),
                )
                session.add(record)
            else:
                record.state = str(data["state"])
                record.failure_count = int(data["failure_count"] or 0)
                record.success_count = int(data["success_count"] or 0)
                record.config_version = str(data["config_version"])
                record.window_started_at = (
                    datetime.fromtimestamp(
                        float(data["window_started_at"] or 0), tz=UTC
                    )
                    if data.get("window_started_at")
                    else None
                )
                record.opened_at = (
                    datetime.fromtimestamp(float(data["opened_at"]), tz=UTC)
                    if data.get("opened_at") is not None
                    else None
                )
                record.half_open_probe_at = (
                    datetime.fromtimestamp(
                        float(data["half_open_probe_at"]), tz=UTC
                    )
                    if data.get("half_open_probe_at") is not None
                    else None
                )
                record.updated_at = now
            await session.commit()
            await session.refresh(record)
            return record

    async def list_breaker_states(self) -> Sequence[CircuitBreakerStateRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(CircuitBreakerStateRecord).order_by(
                    CircuitBreakerStateRecord.dependency
                )
            )
            return result.all()

    # ---- retry attempts ----

    async def get_retry_attempt(self, operation_key: str) -> RetryAttemptRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(RetryAttemptRecord).where(
                    RetryAttemptRecord.operation_key == operation_key
                )
            )

    async def record_retry_attempt(
        self,
        *,
        operation_key: str,
        dependency: str,
        scope: str,
        classification: str,
        error_code: str,
        attempt: int,
        max_attempts: int,
        backoff_seconds: float,
        retry_after_seconds: float | None,
        status: str,
        first_error: str,
        payload_hash: str,
    ) -> RetryAttemptRecord:
        now = _now()
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(RetryAttemptRecord).where(
                    RetryAttemptRecord.operation_key == operation_key
                )
            )
            if record is None:
                record = RetryAttemptRecord(
                    operation_key=operation_key,
                    dependency=dependency,
                    scope=scope,
                    error_classification=classification,
                    error_code=error_code,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    backoff_seconds=backoff_seconds,
                    retry_after_seconds=retry_after_seconds,
                    status=status,
                    first_error=first_error[:2000],
                    first_attempt_at=now,
                    last_attempt_at=now,
                    payload_hash=payload_hash,
                )
                session.add(record)
            else:
                record.error_classification = classification
                record.error_code = error_code
                record.attempt = attempt
                record.max_attempts = max_attempts
                record.backoff_seconds = backoff_seconds
                record.retry_after_seconds = retry_after_seconds
                record.status = status
                record.last_attempt_at = now
                if not record.first_attempt_at:
                    record.first_attempt_at = now
                if not record.first_error:
                    record.first_error = first_error[:2000]
                if payload_hash:
                    record.payload_hash = payload_hash
            await session.commit()
            await session.refresh(record)
            return record

    # ---- dead letters ----

    async def enqueue_dead_letter(
        self,
        *,
        operation_key: str,
        dependency: str,
        scope: str,
        classification: str,
        error_code: str,
        attempts: int,
        payload_hash: str,
        policy_version: str,
        code_version: str,
        recovery_hint: str,
        payload_ref: str = "",
    ) -> DeadLetterItemRecord:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(DeadLetterItemRecord).where(
                    DeadLetterItemRecord.operation_key == operation_key
                )
            )
            if record is None:
                record = DeadLetterItemRecord(
                    operation_key=operation_key,
                    dependency=dependency,
                    scope=scope,
                    error_classification=classification,
                    error_code=error_code,
                    attempts=attempts,
                    payload_hash=payload_hash,
                    policy_version=policy_version,
                    code_version=code_version,
                    recovery_hint=recovery_hint,
                    payload_ref=payload_ref,
                )
                session.add(record)
            else:
                record.error_classification = classification
                record.error_code = error_code
                record.attempts = attempts
                record.policy_version = policy_version
                record.code_version = code_version
                record.recovery_hint = recovery_hint
                if payload_ref:
                    record.payload_ref = payload_ref
            await session.commit()
            await session.refresh(record)
            return record

    async def list_dead_letters(
        self, status: str | None = None
    ) -> Sequence[DeadLetterItemRecord]:
        async with self._database.session_factory() as session:
            query = select(DeadLetterItemRecord)
            if status is not None:
                query = query.where(DeadLetterItemRecord.status == status)
            result = await session.scalars(
                query.order_by(DeadLetterItemRecord.created_at.desc())
            )
            return result.all()

    async def get_dead_letter(self, dead_letter_id: str) -> DeadLetterItemRecord | None:
        async with self._database.session_factory() as session:
            return await session.get(DeadLetterItemRecord, dead_letter_id)

    async def update_dead_letter(
        self,
        dead_letter_id: str,
        *,
        status: str,
        recovery_hint: str = "",
        authorization: dict[str, Any] | None = None,
    ) -> DeadLetterItemRecord | None:
        async with self._database.session_factory() as session:
            if authorization is not None:
                rows = await self._consume_authorization_in_session(
                    session, authorization
                )
                if rows != 1:
                    raise ApplicationError(
                        "authorization already consumed or scope mismatch",
                        code="authorization_already_consumed",
                    )
            record = await session.get(DeadLetterItemRecord, dead_letter_id)
            if record is None:
                return None
            record.status = status
            if recovery_hint:
                record.recovery_hint = recovery_hint
            if status in {"resolved", "discarded"}:
                record.resolved_at = _now()
            await session.commit()
            await session.refresh(record)
            return record

    # ---- incidents ----

    async def create_incident(
        self,
        *,
        title: str,
        severity: str,
        impact: str = "",
        metrics: dict[str, Any] | None = None,
        timeline: list[dict[str, Any]] | None = None,
        actions: list[dict[str, Any]] | None = None,
        kill_switch_ids: list[str] | None = None,
    ) -> IncidentRecord:
        async with self._database.session_factory() as session:
            record = IncidentRecord(
                title=title,
                severity=severity,
                impact=impact,
                metrics_json=metrics or {},
                timeline_json=timeline or [],
                actions_json=actions or [],
                kill_switch_ids=kill_switch_ids or [],
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_incidents(
        self, status: str | None = None
    ) -> Sequence[IncidentRecord]:
        async with self._database.session_factory() as session:
            query = select(IncidentRecord)
            if status is not None:
                query = query.where(IncidentRecord.status == status)
            result = await session.scalars(
                query.order_by(IncidentRecord.created_at.desc())
            )
            return result.all()

    async def get_incident(self, incident_id: str) -> IncidentRecord | None:
        async with self._database.session_factory() as session:
            return await session.get(IncidentRecord, incident_id)

    async def append_incident_entry(
        self,
        incident_id: str,
        *,
        entry: dict[str, Any],
        kind: str = "timeline",
    ) -> IncidentRecord | None:
        async with self._database.session_factory() as session:
            record = await session.get(IncidentRecord, incident_id)
            if record is None:
                return None
            if kind == "timeline":
                record.timeline_json = list(record.timeline_json or []) + [entry]
            elif kind == "action":
                record.actions_json = list(record.actions_json or []) + [entry]
            elif kind == "metric":
                merged = dict(record.metrics_json or {})
                merged.update(entry)
                record.metrics_json = merged
            await session.commit()
            await session.refresh(record)
            return record

    async def close_incident(
        self,
        incident_id: str,
        *,
        recovery: dict[str, Any] | None = None,
        retro: dict[str, Any] | None = None,
    ) -> IncidentRecord | None:
        async with self._database.session_factory() as session:
            record = await session.get(IncidentRecord, incident_id)
            if record is None:
                return None
            record.status = "closed"
            if recovery is not None:
                record.recovery_json = recovery
            if retro is not None:
                record.retro_json = retro
            record.closed_at = _now()
            await session.commit()
            await session.refresh(record)
            return record

    # ---- kill switches ----
    # M21/M22: 运维操作（开启/关闭开关、死信重放）必须同一事务内原子消费
    # 一次性授权，杜绝同一审批重复用于多个操作。

    @staticmethod
    async def _consume_authorization_in_session(
        session: Any,
        authorization: dict[str, Any],
    ) -> int:
        """事务内消费授权（与业务变更同一事务，防重放窗口为 0）。"""
        from sqlalchemy import update as sa_update

        from app.infrastructure.database.models import ExecutionAuthorizationRecord

        now = _now()
        result = await session.execute(
            sa_update(ExecutionAuthorizationRecord)
            .where(
                ExecutionAuthorizationRecord.approval_id
                == authorization["approval_id"],
                ExecutionAuthorizationRecord.action_family
                == authorization["action_family"],
                ExecutionAuthorizationRecord.resource_id
                == authorization["resource_id"],
                ExecutionAuthorizationRecord.argument_hash
                == authorization["argument_hash"],
                ExecutionAuthorizationRecord.consumed_at.is_(None),
                or_(
                    ExecutionAuthorizationRecord.expires_at.is_(None),
                    ExecutionAuthorizationRecord.expires_at >= now,
                ),
            )
            .values(consumed_at=now)
        )
        return int(result.rowcount or 0)

    async def create_kill_switch(
        self,
        *,
        scope: str,
        target: str,
        reason: str,
        actor: str,
        approval_id: str | None = None,
        authorization: dict[str, Any] | None = None,
    ) -> KillSwitchRecord:
        async with self._database.session_factory() as session:
            if authorization is not None:
                rows = await self._consume_authorization_in_session(
                    session, authorization
                )
                if rows != 1:
                    raise ApplicationError(
                        "authorization already consumed or scope mismatch",
                        code="authorization_already_consumed",
                    )
            record = await session.scalar(
                select(KillSwitchRecord).where(
                    KillSwitchRecord.scope == scope,
                    KillSwitchRecord.target == target,
                )
            )
            if record is None:
                record = KillSwitchRecord(
                    scope=scope,
                    target=target,
                    status="on",
                    reason=reason,
                    actor=actor,
                    approval_id=approval_id,
                )
                session.add(record)
            else:
                record.status = "on"
                record.reason = reason
                record.actor = actor
                record.approval_id = approval_id
                record.disabled_at = None
            await session.commit()
            await session.refresh(record)
            return record

    async def list_kill_switches(
        self, active_only: bool = False
    ) -> Sequence[KillSwitchRecord]:
        async with self._database.session_factory() as session:
            query = select(KillSwitchRecord)
            if active_only:
                query = query.where(KillSwitchRecord.status == "on")
            result = await session.scalars(
                query.order_by(
                    KillSwitchRecord.scope,
                    KillSwitchRecord.target,
                )
            )
            return result.all()

    async def disable_kill_switch(
        self,
        kill_switch_id: str,
        *,
        actor: str,
        reason: str,
        authorization: dict[str, Any] | None = None,
    ) -> KillSwitchRecord | None:
        async with self._database.session_factory() as session:
            if authorization is not None:
                rows = await self._consume_authorization_in_session(
                    session, authorization
                )
                if rows != 1:
                    raise ApplicationError(
                        "authorization already consumed or scope mismatch",
                        code="authorization_already_consumed",
                    )
            record = await session.get(KillSwitchRecord, kill_switch_id)
            if record is None:
                return None
            record.status = "off"
            record.actor = actor
            record.reason = reason
            record.disabled_at = _now()
            await session.commit()
            await session.refresh(record)
            return record

    async def is_killed(self, scope: str, target: str) -> tuple[bool, str]:
        """按层级判定是否被停止（含 global/dependency 覆盖）。"""
        switches = await self.list_kill_switches(active_only=True)
        return kill_switch_active(
            [
                {"scope": s.scope, "target": s.target, "status": s.status}
                for s in switches
            ],
            scope=scope,
            target=target,
        )
