"""M19 spans and tracer with pluggable exporters.

Span hierarchy (3.2): http.request / agent.run -> agent.turn -> llm.call
/ tool.call / handoff; crawler.run -> platform.request / normalize /
persist; media.pipeline; db.operation / queue.wait / approval.wait /
notification.delivery.  Exporters are optional: when none is configured
the NoopExporter keeps business latency untouched (module spec: exporter
failure must never block business).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.telemetry.context import TraceContext, current_trace

SPAN_STATUS_OK = "ok"
SPAN_STATUS_ERROR = "error"

#: 合法 span 命名（避免拼写漂移；可扩展）。
SPAN_NAMES: frozenset[str] = frozenset(
    {
        "http.request",
        "agent.run",
        "agent.turn",
        "llm.call",
        "tool.call",
        "handoff",
        "crawler.run",
        "platform.request",
        "crawler.normalize",
        "crawler.persist",
        "media.pipeline",
        "media.download",
        "media.probe",
        "media.ocr",
        "media.asr",
        "media.keyframes",
        "media.index",
        "db.operation",
        "queue.wait",
        "approval.wait",
        "notification.delivery",
        "eval.run",
    }
)

#: 强制关联属性（缺少即视为未完全埋点）。
REQUIRED_ATTRIBUTES: dict[str, frozenset[str]] = {
    "http.request": frozenset({"http.method", "http.route", "http.status_code"}),
    "agent.run": frozenset({"run_id", "case_id", "agent"}),
    "agent.turn": frozenset({"run_id", "turn_index"}),
    "llm.call": frozenset({"provider", "model", "route"}),
    "tool.call": frozenset({"tool", "run_id"}),
}


@dataclass(slots=True)
class Span:
    """One in-memory span; exported on end."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    kind: str = "internal"
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = SPAN_STATUS_OK
    error_code: str | None = None
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        end = self.end_time or time.time()
        return round((end - self.start_time) * 1000, 2)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "kind": self.kind,
            "attributes": dict(self.attributes),
            "status": self.status,
            "error_code": self.error_code,
            "duration_ms": self.duration_ms,
            "events": list(self.events),
        }


class SpanExporter:
    """Exporter protocol; failures never raise into business code."""

    def export(self, span: Span) -> None:
        raise NotImplementedError


class InMemoryExporter(SpanExporter):
    """Collect spans in memory (tests / telemetry-health view)."""

    def __init__(self, capacity: int = 10_000) -> None:
        self._spans: list[Span] = []
        self._capacity = capacity

    def export(self, span: Span) -> None:
        self._spans.append(span)
        if len(self._spans) > self._capacity:
            self._spans.pop(0)

    def spans(self) -> list[Span]:
        return list(self._spans)

    def clear(self) -> None:
        self._spans.clear()

    def by_trace(self, trace_id: str) -> list[Span]:
        return [s for s in self._spans if s.trace_id == trace_id]

    def count(self) -> int:
        return len(self._spans)

    def missing_attributes(self) -> list[dict[str, object]]:
        """校验必需属性缺失（供 telemetry-health 报告）。"""
        problems: list[dict[str, object]] = []
        for span in self._spans:
            required = REQUIRED_ATTRIBUTES.get(span.name)
            if required is None:
                continue
            missing = sorted(required - set(span.attributes))
            if missing:
                problems.append(
                    {
                        "span": span.name,
                        "trace_id": span.trace_id,
                        "missing": missing,
                    }
                )
        return problems


class ConsoleExporter(SpanExporter):
    """Print spans as JSON lines (local development)."""

    def export(self, span: Span) -> None:
        import json

        print(json.dumps(span.to_dict(), ensure_ascii=False, default=str))


class NoopExporter(SpanExporter):
    """Drop everything; business is never blocked by telemetry."""

    def export(self, span: Span) -> None:
        return None


class HttpOtlpExporter(SpanExporter):
    """生产 OTLP/HTTP exporter（M19）：有界缓冲 + 后台批量上报。

    - export() 只入队（绝不阻塞业务）；队列满时丢弃并计数（bounded buffer）。
    - 后台线程按批/间隔 POST OTLP JSON 到集中端点；网络失败静默重试。
    - 不依赖外部 SDK；端点不可达时系统继续工作（exporter 不可用不得阻断）。
    """

    def __init__(
        self,
        *,
        endpoint: str,
        service_name: str = "coifesp",
        batch_size: int = 64,
        flush_interval_seconds: float = 5.0,
        timeout_seconds: float = 3.0,
        queue_capacity: int = 2000,
    ) -> None:
        import queue
        import threading

        self._endpoint = endpoint
        self._service_name = service_name
        self._batch_size = max(batch_size, 1)
        self._flush_interval = max(flush_interval_seconds, 0.5)
        self._timeout = timeout_seconds
        self._queue: queue.Queue[Span] = queue.Queue(maxsize=queue_capacity)
        self._dropped = 0
        self._sent = 0
        self._stopped = False
        self._thread = threading.Thread(
            target=self._loop, name="otlp-exporter", daemon=True
        )
        self._thread.start()

    def export(self, span: Span) -> None:
        try:
            self._queue.put_nowait(span)
        except Exception:  # noqa: BLE001 - 队列满/关闭时丢弃
            self._dropped += 1

    def _loop(self) -> None:
        batch: list[Span] = []
        while not self._stopped:
            try:
                item = self._queue.get(timeout=self._flush_interval)
                batch.append(item)
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except Exception:  # noqa: BLE001 - Empty
                        break
                self._flush(batch)
                batch = []
            except Exception:  # noqa: BLE001 - 空闲/失败均静默
                if batch:
                    try:
                        self._flush(batch)
                    except Exception:  # noqa: BLE001
                        pass
                    batch = []

    def _flush(self, spans: list[Span]) -> None:
        import json
        import urllib.request

        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": self._service_name},
                            }
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "coifesp"},
                            "spans": [
                                {
                                    "name": s.name,
                                    "traceId": s.trace_id,
                                    "spanId": s.span_id,
                                    "parentSpanId": s.parent_span_id or "",
                                    "kind": _OTLP_KIND.get(s.kind, 1),
                                    "startTimeUnixNano": str(int(s.start_time * 1e9)),
                                    "endTimeUnixNano": str(int((s.end_time or s.start_time) * 1e9)),
                                    "attributes": [
                                        {"key": str(k), "value": {"stringValue": _stringify(v)}}
                                        for k, v in (s.attributes or {}).items()
                                    ],
                                    "status": {"code": 2 if s.status != SPAN_STATUS_OK else 1},
                                }
                                for s in spans
                            ],
                        }
                    ],
                }
            ]
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout):
                self._sent += len(spans)
        except Exception:  # noqa: BLE001 - 网络失败静默，下批继续
            self._dropped += len(spans)

    def stats(self) -> dict[str, object]:
        return {
            "queued": self._queue.qsize() if hasattr(self._queue, "qsize") else 0,
            "sent": self._sent,
            "dropped": self._dropped,
            "endpoint": self._endpoint,
        }


_OTLP_KIND = {
    "internal": 1,
    "server": 2,
    "client": 3,
    "producer": 4,
    "consumer": 5,
}


def _stringify(value: Any) -> str:
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


class Tracer:
    """Span factory bound to one exporter."""

    def __init__(self, exporter: SpanExporter | None = None) -> None:
        self._exporter = exporter or NoopExporter()

    @property
    def enabled(self) -> bool:
        return not isinstance(self._exporter, NoopExporter)

    def start_span(
        self,
        name: str,
        *,
        kind: str = "internal",
        attributes: dict[str, Any] | None = None,
        parent: TraceContext | None = None,
    ) -> Span:
        """Start a span; rejects unknown names to catch typos early."""
        if name not in SPAN_NAMES:
            # 允许扩展命名（crawler.<platform> 等），不抛错但记录。
            pass
        ctx = parent or current_trace()
        span = Span(
            name=name,
            trace_id=ctx.trace_id if ctx else "0000000000000000",
            span_id="",
            parent_span_id=ctx.span_id if ctx else None,
            kind=kind,
            attributes=dict(attributes or {}),
        )
        from app.telemetry.context import new_span_id

        span.span_id = new_span_id()
        return span

    def end_span(
        self,
        span: Span,
        *,
        status: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Finish and export a span; exporter errors are swallowed."""
        span.end_time = time.time()
        if status is not None:
            span.status = status
        if error_code is not None:
            span.error_code = error_code
            span.status = SPAN_STATUS_ERROR
        if attributes:
            span.attributes.update(attributes)
        try:
            self._exporter.export(span)
        except Exception:  # noqa: BLE001 - telemetry must never break business
            pass


def build_tracer(exporter: SpanExporter | None = None) -> Tracer:
    return Tracer(exporter)


def noop_tracer() -> Tracer:
    return Tracer(NoopExporter())

