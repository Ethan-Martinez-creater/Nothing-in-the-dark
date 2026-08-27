"""M19 telemetry context: trace/span correlation fields.

OTel-compatible naming: trace_id/span_id are 16-byte hex; the
context propagates through async tasks via a ContextVar.  High-cardinality
fields (post ids, full URLs, error text) never become metric labels - they
may appear as controlled span attributes or log fields only.
"""

from __future__ import annotations

import contextvars
import secrets
from dataclasses import dataclass, field
from typing import Any

#: 关联字段（3.1）：request_id / case_id / goal_id / plan_step_id /
#: run_id / parent_run_id / tool_call_id / task/job id / platform /
#: provider / model / worker_id 由调用方作为 span attribute 提供。
CORRELATION_KEYS: frozenset[str] = frozenset(
    {
        "request_id",
        "case_id",
        "goal_id",
        "plan_step_id",
        "run_id",
        "parent_run_id",
        "tool_call_id",
        "task_id",
        "job_id",
        "platform",
        "provider",
        "model",
        "worker_id",
    }
)

#: 禁止作为 metric label 的高基数字段。
HIGH_CARDINALITY_FIELDS: frozenset[str] = frozenset(
    {"post_id", "url", "error_text", "prompt", "arguments", "output"}
)


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Immutable correlation context for one logical operation."""

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def child(self) -> TraceContext:
        """Child span context: same trace, new span under this one."""
        return TraceContext(
            trace_id=self.trace_id,
            span_id=new_span_id(),
            parent_span_id=self.span_id,
            attributes=dict(self.attributes),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id or "",
        }


_current: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar(
    "dsh_trace_context", default=None
)


def new_trace_id() -> str:
    return secrets.token_hex(8)


def new_span_id() -> str:
    return secrets.token_hex(8)


def current_trace() -> TraceContext | None:
    return _current.get()


def set_trace(context: TraceContext | None) -> contextvars.Token:
    return _current.set(context)


def reset_trace(token: contextvars.Token) -> None:
    _current.reset(token)


def root_context(*, attributes: dict[str, Any] | None = None) -> TraceContext:
    return TraceContext(
        trace_id=new_trace_id(),
        span_id=new_span_id(),
        attributes=dict(attributes or {}),
    )

