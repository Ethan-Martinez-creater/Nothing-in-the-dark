"""M19 telemetry package: context, spans, metrics, redaction and SLOs."""

from app.telemetry.context import (
    TraceContext,
    current_trace,
    new_span_id,
    new_trace_id,
    reset_trace,
    root_context,
    set_trace,
)
from app.telemetry.exporter_factory import build_exporter, build_telemetry
from app.telemetry.metrics import MetricRegistry
from app.telemetry.redact import (
    redact_exception_chain,
    redact_text,
    redact_value,
    scan_for_canary_secrets,
)
from app.telemetry.slo import DEFAULT_SLOS, SLO, evaluate_slos
from app.telemetry.tracer import (
    ConsoleExporter,
    InMemoryExporter,
    NoopExporter,
    Span,
    Tracer,
    build_tracer,
    noop_tracer,
)

__all__ = [
    "TraceContext",
    "Span",
    "Tracer",
    "MetricRegistry",
    "InMemoryExporter",
    "ConsoleExporter",
    "NoopExporter",
    "SLO",
    "DEFAULT_SLOS",
    "evaluate_slos",
    "build_exporter",
    "build_telemetry",
    "current_trace",
    "new_span_id",
    "new_trace_id",
    "reset_trace",
    "root_context",
    "set_trace",
    "build_tracer",
    "noop_tracer",
    "redact_value",
    "redact_text",
    "redact_exception_chain",
    "scan_for_canary_secrets",
]

