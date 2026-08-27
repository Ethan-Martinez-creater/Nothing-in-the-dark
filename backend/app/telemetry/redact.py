"""M19 log/trace redaction: allowlist + unified redaction processor.

Cookies, Authorization, API keys, prompt/tool payloads and exception
chains are redacted before they reach logs, spans or metrics.  A
canary-scan helper supports startup fingerprint scanning for known
secrets (module spec 5).
"""

from __future__ import annotations

import re

REDACTED = "***"

#: 值级秘密模式（键值对形式）。
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|authorization|"
    r"secret[_-]?key|password|client[_-]?secret|cookie[s]?)\s*[:=]\s*[A-Za-z0-9._~+/=-]{8,}|"
    r"bearer\s+[A-Za-z0-9._~-]{12,})"
)

#: 键名校验（大小写不敏感子串）。
_SENSITIVE_KEY_MARKERS = (
    "cookie",
    "token",
    "secret",
    "password",
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "prompt",
    "arguments",
    "output",
)


def redact_value(value: object, *, key: str = "") -> object:
    """Deep redact sensitive values and long payloads for logs/traces."""
    lowered = key.lower()
    if any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS):
        return REDACTED
    if isinstance(value, str):
        if _SECRET_VALUE_RE.search(value):
            return REDACTED
        return value
    if isinstance(value, dict):
        return {k: redact_value(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v, key=key) for v in value]
    return value


def redact_text(text: str) -> str:
    """Redact secret-looking assignments inside free-form text."""
    return _SECRET_VALUE_RE.sub(REDACTED, text)


CANARY_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)api[_-]?key\s*=\s*[A-Za-z0-9._~-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~-]{12,}"),
    re.compile(r"(?i)authorization:\s*[A-Za-z0-9._~-]{12,}"),
    re.compile(r"(?i)x-?api-?key:\s*[A-Za-z0-9._~-]{12,}"),
)


def scan_for_canary_secrets(payload: str) -> list[str]:
    """Startup fingerprint scan: report suspicious secret-like hits.

    Returns a list of matched pattern names; never the matched value.
    """
    hits: list[str] = []
    for pattern in CANARY_SECRET_PATTERNS:
        if pattern.search(payload):
            hits.append(pattern.pattern[:60])
    return hits


def redact_exception_chain(exc: BaseException) -> str:
    """Redacted exception summary (stack text may contain secrets)."""
    message = redact_text(str(exc))
    return type(exc).__name__ + ": " + message[:500]

