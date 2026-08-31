"""Per-tool progress reporting bridge.

External, long-running tools (e.g. social collection) run platform by
platform and can take many minutes. The runtime owns the run event stream,
but tool handlers only receive an arguments dict. A ContextVar bridges that
gap: the runtime binds a progress callback before invoking the handler and
resets it afterwards, so handlers can emit coarse-grained progress events
without changing their signatures.
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from typing import Any

ProgressSink = Callable[[dict[str, Any]], Awaitable[None]]

_progress_sink: contextvars.ContextVar[ProgressSink | None] = (
    contextvars.ContextVar("tool_progress_sink", default=None)
)


def set_progress_sink(sink: ProgressSink | None) -> contextvars.Token:
    return _progress_sink.set(sink)


def reset_progress_sink(token: contextvars.Token) -> None:
    _progress_sink.reset(token)


async def emit_progress(event: dict[str, Any]) -> None:
    """Fire a progress event when a sink is bound; never raises."""
    sink = _progress_sink.get()
    if sink is None:
        return
    try:
        await sink(event)
    except Exception:
        # Progress reporting must never break the tool itself.
        pass
