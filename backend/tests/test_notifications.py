"""M13 调查结果订阅与外部协作测试。"""

from __future__ import annotations

import atexit
import hashlib
import hmac
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.application.notification_service import NotificationDispatcher
from app.core.config import Settings
from app.main import create_app
from app.services.notifications import (
    EventEnvelope,
    classify_http_status,
    hash_token,
    match_subscription,
    new_share_token,
    sign_payload,
    validate_webhook_url,
    verify_signature,
)

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-notif-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


def _event(event_type: str = "alert.fired", severity: str = "warning") -> EventEnvelope:
    return EventEnvelope(
        event_id="ev-1",
        event_type=event_type,
        occurred_at=datetime.now(UTC),
        case_id="c1",
        severity=severity,
        data={"count": 3},
    )


# ---- 订阅匹配 ------------------------------------------------------------


def test_match_filters_event_type() -> None:
    event = _event("alert.fired")
    matched = match_subscription(
        event=event, event_filters=["alert.fired"], severity="info", quiet_hours={}
    )
    assert matched
    not_matched = match_subscription(
        event=event, event_filters=["monitor.failed"], severity="info", quiet_hours={}
    )
    assert not not_matched


def test_match_severity() -> None:
    event = _event(severity="info")
    low = match_subscription(
        event=event, event_filters=[], severity="warning", quiet_hours={}
    )
    assert low is False
    event_warning = _event(severity="critical")
    high = match_subscription(
        event=event_warning, event_filters=[], severity="warning", quiet_hours={}
    )
    assert high


def test_match_quiet_hours_cross_midnight() -> None:
    event = _event()
    at = datetime(2026, 8, 1, 23, 30, tzinfo=UTC)
    assert (
        match_subscription(
            event=event,
            event_filters=[],
            severity="info",
            quiet_hours={"start": "22:00", "end": "06:00"},
            at=at,
        )
        is False
    )
    day = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    assert (
        match_subscription(
            event=event,
            event_filters=[],
            severity="info",
            quiet_hours={"start": "22:00", "end": "06:00"},
            at=day,
        )
        is True
    )


# ---- Webhook 签名 --------------------------------------------------------


def test_signature_fixed_vector() -> None:
    body = b'{"event_id":"ev-1"}'
    signature = sign_payload("secret", 1_700_000_000, "ev-1", body)
    expected = hmac.new(
        b"secret", b"1700000000.ev-1." + body, hashlib.sha256
    ).hexdigest()
    assert signature == expected


def test_signature_verification_replay_protection() -> None:
    body = b'{"x":1}'
    signature = sign_payload("s", int(time.time()) - 400, "ev-1", body)
    ok, reason = verify_signature(
        secret="s",
        received=signature,
        timestamp=int(time.time()) - 400,
        event_id="ev-1",
        body=body,
        replay_window_seconds=300,
    )
    assert not ok
    assert reason == "timestamp_outside_replay_window"


def test_signature_verification_ok_and_mismatch() -> None:
    body = b'{"x":1}'
    now = int(time.time())
    signature = sign_payload("s", now, "ev-1", body)
    ok, _ = verify_signature(
        secret="s", received=signature, timestamp=now, event_id="ev-1", body=body
    )
    assert ok
    ok2, reason2 = verify_signature(
        secret="wrong",
        received=signature,
        timestamp=now,
        event_id="ev-1",
        body=body,
    )
    assert not ok2
    assert reason2 == "signature_mismatch"


# ---- HTTP 状态分类 -------------------------------------------------------


def test_classify_http_status() -> None:
    assert classify_http_status(200) == "sent"
    assert classify_http_status(429) == "retry_wait"
    assert classify_http_status(500) == "retry_wait"
    assert classify_http_status(400) == "dead"
    assert classify_http_status(404) == "dead"


# ---- SSRF / 分享 token ---------------------------------------------------


def test_webhook_url_rejects_private(monkeypatch: pytest.MonkeyPatch) -> None:
    reason = validate_webhook_url("http://127.0.0.1:8080/hook")
    assert reason is not None


def test_share_token_hash_only() -> None:
    token, token_hash = new_share_token()
    assert token_hash == hash_token(token)
    assert token != token_hash
    assert len(token_hash) == 64


# ---- API -----------------------------------------------------------------


def _client() -> TestClient:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{_tmp_db()}",
        demo_mode=True,
    )
    app = create_app(settings)
    return TestClient(app)


def test_api_subscription_lifecycle() -> None:
    with _client() as client:
        created = client.post(
            "/api/v1/cases/c1/subscriptions",
            json={
                "name": "告警订阅",
                "event_filters": ["alert.fired"],
                "severity": "warning",
                "channel": "inbox",
            },
        )
        assert created.status_code == 201
        sub_id = created.json()["id"]

        listed = client.get("/api/v1/cases/c1/subscriptions")
        assert listed.status_code == 200
        assert len(listed.json()["subscriptions"]) == 1

        paused = client.post(f"/api/v1/cases/c1/subscriptions/{sub_id}:pause")
        assert paused.status_code == 200
        assert paused.json()["enabled"] is False

        resumed = client.post(f"/api/v1/cases/c1/subscriptions/{sub_id}:resume")
        assert resumed.status_code == 200
        assert resumed.json()["enabled"] is True


def test_api_endpoint_rejects_unsafe_url() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/cases/c1/notification-endpoints",
            json={"name": "bad", "url": "http://127.0.0.1:9000/hook"},
        )
        assert response.status_code == 422


def test_api_event_and_notifications() -> None:
    with _client() as client:
        client.post(
            "/api/v1/cases/c1/subscriptions",
            json={"event_filters": ["alert.fired"], "severity": "info", "channel": "inbox"},
        )
        enqueued = client.post(
            "/api/v1/cases/c1/notification-events",
            json={"event_type": "alert.fired", "severity": "warning", "data": {"n": 1}},
        )
        assert enqueued.status_code == 201

        inbox = client.get("/api/v1/cases/c1/notifications")
        assert inbox.status_code == 200
        assert len(inbox.json()["events"]) == 1
        assert inbox.json()["events"][0]["event_type"] == "alert.fired"

        deliveries = client.get("/api/v1/cases/c1/deliveries")
        assert deliveries.status_code == 200


def test_api_share_link_flow() -> None:
    with _client() as client:
        created = client.post(
            "/api/v1/cases/c1/share-links",
            json={"target_type": "artifact", "target_id": "art-1", "expires_in_hours": 1},
        )
        assert created.status_code == 201
        token = created.json()["token"]

        resolved = client.get(f"/api/v1/cases/share-links/{token}")
        assert resolved.status_code == 200
        assert resolved.json()["target_id"] == "art-1"


def test_api_share_link_per_minute_rate_limit() -> None:
    with _client() as client:
        client.app.state.container.notification_service._share_downloads_per_minute = 2
        created = client.post(
            "/api/v1/cases/c1/share-links",
            json={
                "target_type": "artifact",
                "target_id": "art-rate",
                "expires_in_hours": 1,
                "download_limit": 10,
            },
        )
        assert created.status_code == 201
        token = created.json()["token"]
        assert client.get(f"/api/v1/cases/share-links/{token}").status_code == 200
        assert client.get(f"/api/v1/cases/share-links/{token}").status_code == 200
        limited = client.get(f"/api/v1/cases/share-links/{token}")
        assert limited.status_code == 429


def test_api_export_job() -> None:
    with _client() as client:
        created = client.post(
            "/api/v1/cases/c1/export-jobs",
            json={"scope": "case", "format": "json"},
        )
        assert created.status_code == 201
        jobs = client.get("/api/v1/cases/c1/export-jobs")
        assert jobs.status_code == 200
        assert len(jobs.json()["jobs"]) == 1


async def test_dispatcher_retries_and_resolves_signing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    updates: list[dict[str, object]] = []

    class FakeRepository:
        async def update_delivery_status(
            self, delivery_id: str, status: str, **kwargs: object
        ) -> None:
            updates.append({"id": delivery_id, "status": status, **kwargs})

    def handler(request: httpx.Request) -> httpx.Response:
        timestamp = int(request.headers["X-Webhook-Timestamp"])
        expected = sign_payload("resolved-secret", timestamp, "ev-retry", request.content)
        assert request.headers["X-Webhook-Signature"] == expected
        return httpx.Response(500, request=request)

    monkeypatch.setattr(
        "app.services.notifications.validate_webhook_url", lambda _url: None
    )
    dispatcher = NotificationDispatcher(
        FakeRepository(),  # type: ignore[arg-type]
        secret_resolver=lambda ref: "resolved-secret" if ref == "WEBHOOK_KEY" else None,
    )
    await dispatcher._http.aclose()
    dispatcher._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        status = await dispatcher._deliver(
            SimpleNamespace(id="delivery-1", attempt=0),
            SimpleNamespace(
                url="https://example.com/hook",
                secret_ref="WEBHOOK_KEY",
            ),
            EventEnvelope(
                event_id="ev-retry",
                event_type="alert.fired",
                occurred_at=datetime.now(UTC),
                case_id="case-a",
                severity="warning",
                data={"value": 1},
            ),
        )
    finally:
        await dispatcher._http.aclose()
    assert status == "retry_wait"
    assert updates[-1]["status"] == "retry_wait"
    assert updates[-1]["attempt"] == 1
    assert updates[-1]["next_retry_at"] is not None