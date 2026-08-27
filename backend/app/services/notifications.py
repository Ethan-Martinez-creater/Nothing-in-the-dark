"""调查结果订阅与外部协作（13）。

- SubscriptionMatcher：事件类型/严重度/静默时段匹配。
- WebhookSigner：HMAC-SHA256 签名（时间戳 + event_id + 原始 body），
  设置防重放时间窗。
- WebhookValidator：投递前 URL 的协议/DNS/IP/端口/重定向校验（防 SSRF）。
- ShareToken：随机 token 只存哈希，常量时间比较。
- NotificationDispatcher：HTTP 投递、指数退避 + 抖动、4xx 死信 / 5xx 重试、
  幂等 event_id；外部响应永不注入 Agent 上下文。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.harness.sandbox import validate_egress_url

EVENT_SCHEMA_VERSION = "1.0"
MAX_ATTEMPTS = 5
DEAD_HTTP_STATUSES = frozenset({400, 401, 403, 404, 410, 422})
RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """统一领域事件 envelope（13 文档 4 节）。"""

    event_id: str
    event_type: str
    occurred_at: datetime
    case_id: str
    run_id: str | None = None
    severity: str = "info"
    classification: str = "monitoring"
    data: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "schema_version": EVENT_SCHEMA_VERSION,
            "occurred_at": self.occurred_at.isoformat(),
            "case_id": self.case_id,
            "run_id": self.run_id,
            "severity": self.severity,
            "data": self.data,
            "trace_id": self.trace_id,
        }


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------


def _in_quiet_hours(quiet_hours: dict[str, object], at: datetime) -> bool:
    if not quiet_hours:
        return False
    start = str(quiet_hours.get("start") or "")
    end = str(quiet_hours.get("end") or "")
    if not start or not end:
        return False
    try:
        start_h, start_m = (int(x) for x in start.split(":"))
        end_h, end_m = (int(x) for x in end.split(":"))
    except (ValueError, AttributeError):
        return False
    current = at.hour * 60 + at.minute
    start_min = start_h * 60 + start_m
    end_min = end_h * 60 + end_m
    if start_min <= end_min:
        return start_min <= current < end_min
    # 跨午夜
    return current >= start_min or current < end_min


def match_subscription(
    *,
    event: EventEnvelope,
    event_filters: list[str],
    severity: str,
    quiet_hours: dict[str, object],
    allowed_event_types: list[str] | None = None,
    at: datetime | None = None,
) -> bool:
    """事件是否命中订阅。静默时段命中则跳过。"""
    at = at or datetime.now(UTC)
    if event_filters and event.event_type not in event_filters:
        return False
    if allowed_event_types and event.event_type not in allowed_event_types:
        return False
    severity_rank = {"info": 0, "warning": 1, "critical": 2}
    if severity_rank.get(event.severity, 0) < severity_rank.get(severity, 0):
        return False
    if _in_quiet_hours(quiet_hours, at):
        return False
    return True


# ---------------------------------------------------------------------------
# Webhook 签名与校验
# ---------------------------------------------------------------------------


def sign_payload(
    secret: str, timestamp: int, event_id: str, body: bytes
) -> str:
    """HMAC-SHA256：timestamp + event_id + 原始 body。"""
    message = f"{timestamp}.{event_id}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_signature(
    *,
    secret: str,
    received: str,
    timestamp: int,
    event_id: str,
    body: bytes,
    replay_window_seconds: int = 300,
    now: int | None = None,
) -> tuple[bool, str]:
    now = now or int(time.time())
    if abs(now - timestamp) > replay_window_seconds:
        return False, "timestamp_outside_replay_window"
    expected = sign_payload(secret, timestamp, event_id, body)
    if not hmac.compare_digest(expected, received):
        return False, "signature_mismatch"
    return True, "ok"


def validate_webhook_url(url: str) -> str | None:
    """投递前 SSRF 校验；拒绝内网/云元数据/非白名单端口。"""
    return validate_egress_url(url)


# ---------------------------------------------------------------------------
# 分享 token
# ---------------------------------------------------------------------------


def new_share_token() -> tuple[str, str]:
    """生成 (明文 token, sha256 哈希)；明文只返回一次。"""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


# ---------------------------------------------------------------------------
# 投递状态分类
# ---------------------------------------------------------------------------


def classify_http_status(status: int) -> str:
    """HTTP 状态分类：2xx 成功；408/409/429/5xx 可重试；其余 4xx 死信。"""
    if 200 <= status < 300:
        return "sent"
    if status in RETRYABLE_HTTP_STATUSES:
        return "retry_wait"
    if status in DEAD_HTTP_STATUSES:
        return "dead"
    return "retry_wait"


def next_retry_delay(attempt: int, *, jitter: float = 0.2) -> float:
    """指数退避 + 抖动（秒）。"""
    base = min(2 ** (attempt - 1), 60)
    spread = base * jitter
    return base + secrets.SystemRandom().uniform(-spread, spread)


def build_delivery_body(event: EventEnvelope) -> bytes:
    return json.dumps(
        event.to_payload(), ensure_ascii=False, default=str
    ).encode()
