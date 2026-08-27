"""订阅/通知/分享/导出应用服务（13）。

- NotificationService：订阅 CRUD、端点与验证、Outbox 事件入队、
  通知中心、投递历史、分享链接与导出任务。
- NotificationDispatcher：后台 Worker，轮询待投递事件，匹配订阅后
  经 WebhookProvider 投递（幂等 event_id、退避、死信）。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.infrastructure.database.models import (
    DeliveryAttemptRecord,
    ExportJobRecord,
    NotificationEndpointRecord,
    NotificationEventRecord,
    ShareLinkRecord,
    SubscriptionRecord,
)
from app.services import notifications as notifications_domain

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(
        self, repository: ApplicationRepository, *, share_downloads_per_minute: int = 60
    ) -> None:
        self._repository = repository
        self._share_downloads_per_minute = max(1, share_downloads_per_minute)

    # ---- 订阅 -------------------------------------------------------------

    async def create_subscription(
        self,
        *,
        case_id: str,
        name: str,
        event_filters: list[str],
        severity: str = "info",
        channel: str = "inbox",
        endpoint_id: str | None = None,
        schedule: str = "instant",
        quiet_hours: dict[str, object] | None = None,
    ) -> SubscriptionRecord:
        subscription = await self._repository.create_subscription(
            SubscriptionRecord(
                case_id=case_id,
                name=name,
                event_filters=event_filters,
                severity=severity,
                channel=channel,
                endpoint_id=endpoint_id,
                schedule=schedule,
                quiet_hours=quiet_hours or {},
            )
        )
        return subscription

    async def list_subscriptions(self, case_id: str) -> list[SubscriptionRecord]:
        return await self._repository.list_subscriptions(case_id)

    async def set_subscription_enabled(
        self, case_id: str, subscription_id: str, enabled: bool
    ) -> SubscriptionRecord:
        return await self._repository.set_subscription_enabled(case_id, subscription_id, enabled)

    # ---- 端点 -------------------------------------------------------------

    async def create_endpoint(
        self,
        *,
        case_id: str,
        name: str,
        url: str,
        secret_ref: str = "",
        allowed_event_types: list[str] | None = None,
    ) -> NotificationEndpointRecord:
        reason = notifications_domain.validate_webhook_url(url)
        if reason:
            raise ApplicationError(
                f"Webhook URL 未通过 SSRF 校验: {reason}",
                code="webhook_unsafe_url",
            )
        endpoint = await self._repository.create_endpoint(
            NotificationEndpointRecord(
                case_id=case_id,
                name=name,
                url=url,
                secret_ref=secret_ref,
                allowed_event_types=allowed_event_types or [],
                verification_state="unverified",
            )
        )
        return endpoint

    async def list_endpoints(self, case_id: str) -> list[NotificationEndpointRecord]:
        return await self._repository.list_endpoints(case_id)

    async def verify_endpoint(
        self, *, case_id: str, endpoint_id: str
    ) -> NotificationEndpointRecord:
        """Verify endpoint ownership with a challenge-response POST."""
        endpoint = await self._repository.get_endpoint(endpoint_id)
        if endpoint.case_id != case_id:
            raise ApplicationError(
                "notification endpoint not found",
                code="notification_endpoint_not_found",
            )
        reason = notifications_domain.validate_webhook_url(endpoint.url)
        if reason:
            raise ApplicationError(
                f"Webhook URL 未通过 SSRF 校验: {reason}",
                code="webhook_unsafe_url",
            )
        challenge = secrets.token_urlsafe(32)
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.post(
                    endpoint.url,
                    json={
                        "type": "coifesp.webhook_verification",
                        "challenge": challenge,
                    },
                )
            payload = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as exc:
            await self._repository.set_endpoint_verification(endpoint.id, "failed")
            raise ApplicationError(
                "webhook verification request failed",
                code="webhook_verification_failed",
            ) from exc
        if not isinstance(payload, dict) or payload.get("challenge") != challenge:
            await self._repository.set_endpoint_verification(endpoint.id, "failed")
            raise ApplicationError(
                "webhook verification challenge mismatch",
                code="webhook_verification_failed",
            )
        return await self._repository.set_endpoint_verification(endpoint.id, "verified")

    # ---- Outbox 事件 ------------------------------------------------------

    async def enqueue_event(
        self,
        *,
        event_id: str,
        event_type: str,
        case_id: str,
        severity: str = "info",
        classification: str = "monitoring",
        data: dict[str, object] | None = None,
        run_id: str | None = None,
        dedupe_key: str | None = None,
    ) -> NotificationEventRecord:
        """业务事务外的 Outbox 入队（dedupe_key 幂等）。"""
        effective_dedupe = dedupe_key or event_id
        return await self._repository.enqueue_notification_event(
            NotificationEventRecord(
                event_id=event_id,
                event_type=event_type,
                occurred_at=datetime.now(UTC),
                case_id=case_id,
                run_id=run_id,
                severity=severity,
                classification=classification,
                data=data or {},
                dedupe_key=effective_dedupe,
            )
        )

    # ---- 分享链接 ---------------------------------------------------------

    async def create_share_link(
        self,
        *,
        case_id: str,
        target_type: str,
        target_id: str,
        expires_at: datetime | None = None,
        download_limit: int = 0,
    ) -> dict[str, object]:
        token, token_hash = notifications_domain.new_share_token()
        record = await self._repository.create_share_link(
            ShareLinkRecord(
                case_id=case_id,
                target_type=target_type,
                target_id=target_id,
                token_hash=token_hash,
                expires_at=expires_at,
                download_limit=download_limit,
            )
        )
        return {"token": token, "link_id": record.id}

    async def resolve_share_link(self, token: str) -> dict[str, object]:
        """校验分享 token：过期/撤销返回明确错误；成功计入下载次数。"""
        token_hash = notifications_domain.hash_token(token)
        record = await self._repository.get_share_link_by_hash(token_hash)
        if record is None:
            raise ApplicationError("share link not found", code="share_link_not_found")
        now = datetime.now(UTC)

        def _aware(value: datetime | None) -> datetime | None:
            if value is None:
                return None
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value

        if _aware(record.revoked_at) is not None:
            raise ApplicationError("share link revoked", code="share_link_revoked")
        expires = _aware(record.expires_at)
        if expires is not None and expires < now:
            raise ApplicationError("share link expired", code="share_link_expired")
        if record.download_limit > 0 and record.download_count >= record.download_limit:
            raise ApplicationError(
                "share link download limit exceeded", code="share_link_limit_exceeded"
            )
        consumed = await self._repository.consume_share_download(
            record.id, per_minute=self._share_downloads_per_minute, now=now
        )
        if not consumed:
            raise ApplicationError("share link rate limit exceeded", code="share_link_rate_limited")
        return {
            "case_id": record.case_id,
            "target_type": record.target_type,
            "target_id": record.target_id,
        }

    async def revoke_share_link(self, link_id: str) -> None:
        await self._repository.revoke_share_link(link_id)

    # ---- 导出任务 ---------------------------------------------------------

    async def create_export_job(
        self,
        *,
        case_id: str,
        scope: str,
        scope_ref: str = "",
        format: str = "json",
        redaction_policy: str = "standard",
    ) -> ExportJobRecord:
        job = await self._repository.create_export_job(
            ExportJobRecord(
                case_id=case_id,
                scope=scope,
                scope_ref=scope_ref,
                format=format,
                redaction_policy=redaction_policy,
            )
        )
        return job

    async def list_export_jobs(self, case_id: str) -> list[ExportJobRecord]:
        return await self._repository.list_export_jobs(case_id)

    async def get_export_job(self, job_id: str) -> ExportJobRecord:
        return await self._repository.get_export_job(job_id)


class NotificationDispatcher:
    """后台投递 Worker：轮询待投递事件 → 匹配订阅 → HTTP 投递。

    幂等：每个 (event_id, subscription_id) 唯一 delivery；外部 4xx 进死信，
    408/409/429/5xx 退避重试；外部响应 body 仅用于诊断，绝不注入 Agent。
    """

    def __init__(
        self,
        repository: ApplicationRepository,
        *,
        worker_id: str = "local-notify-worker",
        poll_interval_seconds: float = 2.0,
        enabled: bool = True,
        secret_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._repository = repository
        self._worker_id = worker_id
        self._poll_interval = poll_interval_seconds
        self._enabled = enabled
        self._secret_resolver = secret_resolver
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        # trust_env=False：禁止工具/进程借用宿主代理环境变量绕过出口校验。
        self._http = httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False)

    async def start(self) -> None:
        if not self._enabled:
            return
        self._task = asyncio.create_task(self._loop(), name=f"notify-dispatcher:{self._worker_id}")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._http.aclose()

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("notify dispatcher tick failed")
            await asyncio.sleep(self._poll_interval)

    async def tick(self) -> int:
        undelivered = await self._repository.list_undelivered_events(limit=20)
        completed: list[NotificationEventRecord] = []
        for event in undelivered:
            if await self._dispatch_event(event):
                completed.append(event)
        await self._repository.mark_delivered(completed)
        return len(undelivered)

    async def _dispatch_event(self, event: NotificationEventRecord) -> bool:
        envelope = notifications_domain.EventEnvelope(
            event_id=event.event_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at or datetime.now(UTC),
            case_id=event.case_id,
            run_id=event.run_id,
            severity=event.severity,
            classification=event.classification,
            data=dict(event.data or {}),
            trace_id=event.trace_id,
        )
        subscriptions = await self._repository.list_subscriptions(event.case_id)
        endpoints = await self._repository.list_endpoints(event.case_id)
        endpoints_by_id = {e.id: e for e in endpoints}
        has_relevant_subscription = False
        all_terminal = True
        now = datetime.now(UTC)

        for subscription in subscriptions:
            if not subscription.enabled:
                continue
            endpoint = (
                endpoints_by_id.get(subscription.endpoint_id) if subscription.endpoint_id else None
            )
            relevant = notifications_domain.match_subscription(
                event=envelope,
                event_filters=list(subscription.event_filters or []),
                severity=subscription.severity,
                quiet_hours={},
                allowed_event_types=(list(endpoint.allowed_event_types) if endpoint else None),
            )
            if not relevant:
                continue
            has_relevant_subscription = True
            matched_now = notifications_domain.match_subscription(
                event=envelope,
                event_filters=list(subscription.event_filters or []),
                severity=subscription.severity,
                quiet_hours=dict(subscription.quiet_hours or {}),
                allowed_event_types=(list(endpoint.allowed_event_types) if endpoint else None),
            )
            if not matched_now:
                all_terminal = False
                continue
            if subscription.channel == "webhook":
                if (
                    endpoint is None
                    or not endpoint.enabled
                    or endpoint.verification_state != "verified"
                ):
                    all_terminal = False
                    continue

            delivery = await self._repository.get_or_create_delivery(
                DeliveryAttemptRecord(
                    event_id=event.event_id,
                    subscription_id=subscription.id,
                    endpoint_id=endpoint.id if endpoint else "",
                )
            )
            if delivery is None:
                all_terminal = False
                continue
            if delivery.status in {"sent", "dead"}:
                continue
            retry_at = delivery.next_retry_at
            if retry_at is not None and retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            if retry_at is not None and retry_at > now:
                all_terminal = False
                continue
            if endpoint is None:
                await self._repository.update_delivery_status(delivery.id, "sent", http_status=200)
                continue
            status = await self._deliver(delivery, endpoint, envelope)
            if status not in {"sent", "dead"}:
                all_terminal = False

        return not has_relevant_subscription or all_terminal

    async def _deliver(
        self,
        delivery: DeliveryAttemptRecord,
        endpoint: NotificationEndpointRecord,
        envelope: notifications_domain.EventEnvelope,
    ) -> str:
        url = endpoint.url
        reason = notifications_domain.validate_webhook_url(url)
        if reason:
            await self._repository.update_delivery_status(
                delivery.id, "dead", error_code="ssrf_rejected"
            )
            return "dead"
        secret = self._resolve_secret(endpoint.secret_ref)
        if endpoint.secret_ref and secret is None:
            await self._repository.update_delivery_status(
                delivery.id, "dead", error_code="secret_unavailable"
            )
            return "dead"
        body = notifications_domain.build_delivery_body(envelope)
        timestamp = int(time.time())
        headers = {"Content-Type": "application/json"}
        if secret:
            signature = notifications_domain.sign_payload(
                secret, timestamp, envelope.event_id, body
            )
            headers["X-Webhook-Signature"] = signature
            headers["X-Webhook-Timestamp"] = str(timestamp)
            headers["X-Webhook-Event-Id"] = envelope.event_id
        started = time.monotonic()
        try:
            # M13 DNS rebinding 加固：连接前即时二次解析校验（校验与连接
            # 之间 DNS 变化时拒绝；残余 TOCTOU 窗口由生产网络层兜底）。
            live_reason = notifications_domain.validate_webhook_url(url)
            if live_reason:
                await self._repository.update_delivery_status(
                    delivery.id, "dead", error_code="ssrf_rejected"
                )
                return "dead"
            response = await self._http.post(url, content=body, headers=headers)
            duration = int((time.monotonic() - started) * 1000)
            status = notifications_domain.classify_http_status(response.status_code)
            next_retry = None
            next_attempt = delivery.attempt
            if status == "retry_wait":
                if delivery.attempt >= notifications_domain.MAX_ATTEMPTS:
                    status = "dead"
                else:
                    next_attempt = delivery.attempt + 1
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = max(0.0, float(retry_after)) if retry_after else None
                    except ValueError:
                        delay = None
                    if delay is None:
                        delay = notifications_domain.next_retry_delay(next_attempt)
                    next_retry = datetime.now(UTC) + timedelta(seconds=delay)
            await self._repository.update_delivery_status(
                delivery.id,
                status,
                http_status=response.status_code,
                http_summary=f"HTTP {response.status_code}",
                duration_ms=duration,
                next_retry_at=next_retry,
                attempt=next_attempt,
            )
            return status
        except httpx.HTTPError as exc:
            if delivery.attempt >= notifications_domain.MAX_ATTEMPTS:
                status = "dead"
                next_retry = None
                next_attempt = delivery.attempt
            else:
                status = "retry_wait"
                next_attempt = delivery.attempt + 1
                delay = notifications_domain.next_retry_delay(next_attempt)
                next_retry = datetime.now(UTC) + timedelta(seconds=delay)
            await self._repository.update_delivery_status(
                delivery.id,
                status,
                error_code="http_error",
                http_summary=str(exc)[:150],
                next_retry_at=next_retry,
                attempt=next_attempt,
            )
            return status

    def _resolve_secret(self, secret_ref: str) -> str | None:
        if not secret_ref:
            return ""
        if self._secret_resolver is None:
            return None
        return self._secret_resolver(secret_ref)
