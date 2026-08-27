"""M22: resilience application service.

把 services/resilience.py 的确定性机制接到数据库持久化与 M19 指标：

- 依赖健康矩阵（dependency_health + circuit_breaker_states）。
- 有界重试链（retry_attempts）与死信（dead_letter_items）。
- Kill Switch 层级判定与事故记录（incident_records）。
- 所有判定记录 M19 指标，不解析异常字符串做核心决策。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.infrastructure.database.resilience_repository import ResilienceRepository
from app.services.resilience import (
    AdmissionController,
    CircuitBreaker,
    FailureClassification,
    RetryPolicy,
    classify_exception,
)

logger = logging.getLogger(__name__)


class ResilienceService:
    """故障隔离、降级与事故处置的应用入口。"""

    def __init__(
        self,
        repository: ResilienceRepository,
        settings: Settings,
        telemetry: Any = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._telemetry = telemetry
        self._retry_policy = RetryPolicy(
            max_attempts=settings.resilience_max_attempts,
            base_backoff_seconds=settings.resilience_base_backoff_seconds,
            max_backoff_seconds=settings.resilience_max_backoff_seconds,
            total_time_budget_seconds=settings.resilience_time_budget_seconds,
        )
        self.admission = AdmissionController(
            queue_capacity=settings.resilience_queue_capacity,
            max_wait_seconds=settings.resilience_max_wait_seconds,
            db_watermark=settings.resilience_db_watermark,
            disk_watermark=settings.resilience_disk_watermark,
        )
        self._breakers: dict[tuple[str, str], CircuitBreaker] = {}

    # ---- metrics helpers ----

    def _metric(
        self, name: str, *, labels: dict[str, str] | None = None
    ) -> None:
        if self._telemetry is not None:
            try:
                self._telemetry.metrics.increment(name, labels=labels)
            except Exception:  # noqa: BLE001 - 观测失败不阻断
                pass

    # ---- 依赖健康与熔断 ----

    def classify(
        self,
        exc: BaseException | None = None,
        *,
        status_code: int | None = None,
        scope: str = "tool",
        error_code: str = "",
        retry_after: float | None = None,
    ) -> FailureClassification:
        return classify_exception(
            exc,
            status_code=status_code,
            scope=scope,
            error_code=error_code,
            retry_after=retry_after,
        )

    async def breaker_allows(self, dependency: str, scope: str) -> bool:
        """熔断准入：open 且未到半开探测时拒绝；状态变更持久化。"""
        key = (dependency, scope)
        now = datetime.now(UTC).timestamp()
        if key not in self._breakers:
            record = await self._repository.get_breaker_state(dependency, scope)
            if record is not None:
                breaker = CircuitBreaker.from_dict(
                    {
                        "state": record.state,
                        "failure_count": record.failure_count,
                        "success_count": record.success_count,
                        "opened_at": (
                            record.opened_at.timestamp()
                            if record.opened_at is not None
                            else None
                        ),
                        "half_open_probe_at": (
                            record.half_open_probe_at.timestamp()
                            if record.half_open_probe_at is not None
                            else None
                        ),
                        "config_version": record.config_version,
                    }
                )
            else:
                breaker = CircuitBreaker()
            self._breakers[key] = breaker
        breaker = self._breakers[key]
        allowed = breaker.allow_request(now)
        if not allowed:
            self._metric(
                "resilience.circuit_rejections",
                labels={"dependency": dependency, "scope": scope},
            )
        if breaker.state == "open":
            self._metric(
                "resilience.circuit_open",
                labels={"dependency": dependency, "scope": scope},
            )
        await self._repository.save_breaker_state(
            dependency=dependency, scope=scope, breaker=breaker
        )
        return allowed

    async def record_success(
        self, dependency: str, scope: str, *, error_code: str = ""
    ) -> None:
        now = datetime.now(UTC)
        breaker = self._breakers.get((dependency, scope))
        if breaker is None:
            record = await self._repository.get_breaker_state(dependency, scope)
            breaker = (
                CircuitBreaker.from_dict(
                    {
                        "state": record.state,
                        "failure_count": record.failure_count,
                        "success_count": record.success_count,
                        "opened_at": (
                            record.opened_at.timestamp()
                            if record.opened_at is not None
                            else None
                        ),
                        "half_open_probe_at": (
                            record.half_open_probe_at.timestamp()
                            if record.half_open_probe_at is not None
                            else None
                        ),
                    }
                )
                if record is not None
                else CircuitBreaker()
            )
            self._breakers[(dependency, scope)] = breaker
        breaker.record_success(now.timestamp())
        await self._repository.save_breaker_state(
            dependency=dependency, scope=scope, breaker=breaker
        )
        await self._repository.upsert_dependency_health(
            dependency=dependency,
            scope=scope,
            status="healthy",
            error_code=error_code,
            circuit_state=breaker.state,
            consecutive_failures=0,
            last_success_at=now,
        )

    async def record_failure(
        self,
        dependency: str,
        scope: str,
        classification: FailureClassification,
    ) -> None:
        """失败落库：health + breaker（auth/policy 类错误不入普通熔断）。"""
        now = datetime.now(UTC)
        breaker = self._breakers.get((dependency, scope))
        if breaker is None:
            record = await self._repository.get_breaker_state(dependency, scope)
            breaker = (
                CircuitBreaker.from_dict(
                    {
                        "state": record.state,
                        "failure_count": record.failure_count,
                        "success_count": record.success_count,
                        "opened_at": (
                            record.opened_at.timestamp()
                            if record.opened_at is not None
                            else None
                        ),
                        "half_open_probe_at": (
                            record.half_open_probe_at.timestamp()
                            if record.half_open_probe_at is not None
                            else None
                        ),
                    }
                )
                if record is not None
                else CircuitBreaker()
            )
            self._breakers[(dependency, scope)] = breaker
        status = "outage"
        if classification.classification == "auth_required":
            status = "auth_required"
        elif classification.classification == "policy_denied":
            status = "policy_denied"
        elif classification.classification == "rate_limited":
            status = "degraded"
        if classification.classification not in {"auth_required", "policy_denied"}:
            breaker.record_failure(now.timestamp())
        if breaker.state == "open":
            self._metric(
                "resilience.circuit_open",
                labels={"dependency": dependency, "scope": scope},
            )
        await self._repository.save_breaker_state(
            dependency=dependency, scope=scope, breaker=breaker
        )
        await self._repository.upsert_dependency_health(
            dependency=dependency,
            scope=scope,
            status=status,
            error_code=classification.error_code,
            circuit_state=breaker.state,
            consecutive_failures=breaker.failure_count,
            last_failure_at=now,
        )

    # ---- 有界重试与死信 ----

    async def should_retry(
        self,
        *,
        operation_key: str,
        dependency: str,
        scope: str,
        classification: FailureClassification,
        payload_hash: str,
        first_error: str,
    ) -> tuple[bool, float, str]:
        """按分类/策略判定是否重试，返回 (retry, backoff, status)。"""
        if not classification.retryable:
            return False, 0.0, "permanent"
        record = await self._repository.get_retry_attempt(operation_key)
        attempt = (record.attempt if record is not None else 0) + 1
        if self._retry_policy.exhausted(attempt):
            self._metric(
                "resilience.dead_lettered",
                labels={"dependency": dependency, "scope": scope},
            )
            return False, 0.0, "dead_lettered"
        backoff = self._retry_policy.next_backoff(
            attempt, retry_after=classification.retry_after_seconds
        )
        await self._repository.record_retry_attempt(
            operation_key=operation_key,
            dependency=dependency,
            scope=scope,
            classification=classification.classification,
            error_code=classification.error_code,
            attempt=attempt,
            max_attempts=self._retry_policy.max_attempts,
            backoff_seconds=backoff,
            retry_after_seconds=classification.retry_after_seconds,
            status="pending",
            first_error=first_error,
            payload_hash=payload_hash,
        )
        self._metric(
            "resilience.retries",
            labels={"dependency": dependency, "scope": scope},
        )
        return True, backoff, "pending"

    async def enqueue_dead_letter(
        self,
        *,
        operation_key: str,
        dependency: str,
        scope: str,
        classification: FailureClassification,
        attempts: int,
        payload_hash: str,
        recovery_hint: str,
        payload_ref: str = "",
    ) -> Any:
        self._metric(
            "resilience.dead_lettered",
            labels={"dependency": dependency, "scope": scope},
        )
        return await self._repository.enqueue_dead_letter(
            operation_key=operation_key,
            dependency=dependency,
            scope=scope,
            classification=classification.classification,
            error_code=classification.error_code,
            attempts=attempts,
            payload_hash=payload_hash,
            policy_version=self._settings.resilience_policy_version,
            code_version=self._settings.app_version,
            recovery_hint=recovery_hint,
            payload_ref=payload_ref,
        )

    # ---- Kill Switch 与事故 ----

    async def is_killed(self, scope: str, target: str) -> tuple[bool, str]:
        return await self._repository.is_killed(scope, target)

    async def open_incident(
        self,
        *,
        title: str,
        severity: str = "warning",
        impact: str = "",
        metrics: dict[str, Any] | None = None,
    ) -> Any:
        self._metric("resilience.incident_opened")
        return await self._repository.create_incident(
            title=title,
            severity=severity,
            impact=impact,
            metrics=metrics,
            timeline=[{"at": datetime.now(UTC).isoformat(), "event": "opened"}],
        )

    async def health_summary(self) -> dict[str, object]:
        records = await self._repository.list_dependency_health()
        summary: dict[str, object] = {
            "healthy": 0,
            "degraded": 0,
            "outage": 0,
            "auth_required": 0,
            "policy_denied": 0,
            "dependencies": [],
        }
        counts = summary
        for record in records:
            status = record.status
            counts[status] = int(counts[status]) + 1  # type: ignore[index]
            counts["dependencies"].append(  # type: ignore[union-attr]
                {
                    "dependency": record.dependency,
                    "scope": record.scope,
                    "status": status,
                    "error_code": record.error_code,
                    "circuit_state": record.circuit_state,
                    "consecutive_failures": record.consecutive_failures,
                    "last_success_at": (
                        record.last_success_at.isoformat()
                        if record.last_success_at
                        else None
                    ),
                    "last_failure_at": (
                        record.last_failure_at.isoformat()
                        if record.last_failure_at
                        else None
                    ),
                }
            )
        return summary
