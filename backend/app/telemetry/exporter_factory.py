"""M19 exporter factory: OTLP-ready with no-op fallback.

Local development defaults to console/no-op; an OTLP exporter can be
plugged in via the settings without changing business code.  Exporter
failures never block business (module spec: exporter unavailable must not
block; buffering is bounded).
"""

from __future__ import annotations

from app.telemetry.metrics import MetricRegistry
from app.telemetry.tracer import (
    ConsoleExporter,
    HttpOtlpExporter,
    InMemoryExporter,
    NoopExporter,
    SpanExporter,
    Tracer,
)

EXPORTER_CONSOLE = "console"
EXPORTER_IN_MEMORY = "in_memory"
EXPORTER_NOOP = "noop"
EXPORTER_OTLP_HTTP = "otlp_http"


def build_exporter(
    kind: str | None,
    *,
    otlp_endpoint: str | None = None,
    otlp_service_name: str = "coifesp",
) -> SpanExporter:
    """按配置构建 exporter；未知配置回退 noop（不抛错）。

    otlp_http 需要 otlp_endpoint 非空，否则回退 noop 并保持业务不中断。
    """
    if kind == EXPORTER_CONSOLE:
        return ConsoleExporter()
    if kind == EXPORTER_IN_MEMORY:
        return InMemoryExporter()
    if kind == EXPORTER_OTLP_HTTP:
        if otlp_endpoint:
            return HttpOtlpExporter(
                endpoint=otlp_endpoint,
                service_name=otlp_service_name,
            )
        return NoopExporter()
    return NoopExporter()


class Telemetry:
    """绑定的 tracer + metrics 聚合。"""

    def __init__(
        self,
        *,
        exporter_kind: str | None = None,
        tracer: Tracer | None = None,
        metrics: MetricRegistry | None = None,
        otlp_endpoint: str | None = None,
        otlp_service_name: str = "coifesp",
    ) -> None:
        self.exporter = (
            tracer._exporter
            if tracer is not None
            else build_exporter(
                exporter_kind,
                otlp_endpoint=otlp_endpoint,
                otlp_service_name=otlp_service_name,
            )
        )
        self.tracer = tracer or Tracer(self.exporter)
        self.metrics = metrics or MetricRegistry()

    def snapshot(self) -> dict[str, object]:
        spans = None
        exporter = self.exporter
        if isinstance(exporter, InMemoryExporter):
            spans = [s.to_dict() for s in exporter.spans()[-200:]]
        return {
            "tracer": "otlp-compatible",
            "exporter": type(self.exporter).__name__,
            "enabled": self.tracer.enabled,
            "metrics": self.metrics.snapshot(),
            "recent_spans": spans,
        }

    def health(self) -> dict[str, object]:
        exporter = self.exporter
        missing = exporter.missing_attributes() if hasattr(exporter, "missing_attributes") else []
        span_count = exporter.count() if hasattr(exporter, "count") else 0
        return {
            "status": "ok" if self.tracer.enabled else "noop",
            "exporter": type(exporter).__name__,
            "span_count": span_count,
            "missing_attribute_spans": missing[:20],
            "missing_attribute_count": len(missing),
        }


def build_telemetry(
    *,
    exporter_kind: str | None = None,
    otlp_endpoint: str | None = None,
    otlp_service_name: str = "coifesp",
) -> Telemetry:
    return Telemetry(
        exporter_kind=exporter_kind,
        otlp_endpoint=otlp_endpoint,
        otlp_service_name=otlp_service_name,
    )

