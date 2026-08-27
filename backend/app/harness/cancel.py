"""Run-scoped cancellation token visible to tool handlers."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

run_cancel_event: ContextVar[asyncio.Event | None] = ContextVar(
    "run_cancel_event", default=None
)


def current_cancel_event() -> asyncio.Event | None:
    return run_cancel_event.get()


def crawl_cancelled(event: asyncio.Event | None = None) -> bool:
    token = event if event is not None else current_cancel_event()
    return token is not None and token.is_set()
