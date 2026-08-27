"""M19 metric registry with label allowlists.

Counters and histograms keyed by metric name; labels are restricted to a
controlled allowlist so high-cardinality fields (post_id / URL / error
text) can never inflate cardinality.  Snapshots feed the /system/
telemetry-health view and SLO calculations.
"""

from __future__ import annotations

from collections import defaultdict

from app.telemetry.context import HIGH_CARDINALITY_FIELDS

#: 允许的 metric label 键（4.1 核心指标）。
ALLOWED_LABELS: frozenset[str] = frozenset(
    {
        "kind",
        "route",
        "platform",
        "provider",
        "model",
        "worker",
        "queue",
        "stage",
        "status",
        "outcome",
        "error_code",
        "dependency",
        "scope",
    }
)

#: 核心指标目录（metric name -> 类型）。
METRIC_TYPES: dict[str, str] = {
    "api.requests": "counter",
    "api.errors": "counter",
    "api.latency_ms": "histogram",
    "agent.runs": "counter",
    "agent.runs_ok": "counter",
    "agent.runs_failed": "counter",
    "agent.runs_cancelled": "counter",
    "agent.turns": "counter",
    "agent.tool_calls": "counter",
    "agent.completion_satisfied": "counter",
    "llm.calls": "counter",
    "llm.errors": "counter",
    "llm.retries": "counter",
    "llm.latency_ms": "histogram",
    "llm.tokens_input": "counter",
    "llm.tokens_output": "counter",
    "llm.cost_cny": "counter",
    "crawler.runs": "counter",
    "crawler.success": "counter",
    "crawler.empty": "counter",
    "crawler.errors": "counter",
    "crawler.duration_ms": "histogram",
    "queue.depth": "gauge",
    "queue.wait_ms": "histogram",
    "worker.lease_recovered": "counter",
    "worker.lease_stale": "counter",
    "guardrail.decisions": "counter",
    "guardrail.denied": "counter",
    "guardrail.require_approval": "counter",
    "approval.created": "counter",
    "approval.decided": "counter",
    "approval.expired": "counter",
    "quality.low_confidence": "counter",
    "quality.human_override": "counter",
    "quality.citation_failures": "counter",
    "eval.runs": "counter",
    "eval.gate_blocked": "counter",
    "resilience.circuit_open": "counter",
    "resilience.circuit_rejections": "counter",
    "resilience.retries": "counter",
    "resilience.dead_lettered": "counter",
    "resilience.incident_opened": "counter",
    "resilience.kill_switch_on": "counter",
    "queue.rejected": "counter",
    "queue.deferred": "counter",
    "memory.writes_blocked": "counter",
    "memory.conflicts": "counter",
    "memory.mutations": "counter",
}


class MetricRegistry:
    """In-memory metrics; labels validated against the allowlist."""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, frozenset[tuple[str, str]]], int] = defaultdict(int)
        self._histograms: dict[tuple[str, frozenset[tuple[str, str]]], list[float]] = (
            defaultdict(list)
        )
        self._gauges: dict[tuple[str, frozenset[tuple[str, str]]], float] = defaultdict(float)

    def increment(
        self, name: str, value: int = 1, *, labels: dict[str, str] | None = None
    ) -> None:
        self._validate(name, labels)
        key = self._key(name, labels)
        self._counters[key] += value

    def observe(
        self, name: str, value: float, *, labels: dict[str, str] | None = None
    ) -> None:
        self._validate(name, labels)
        key = self._key(name, labels)
        self._histograms[key].append(float(value))

    def set_gauge(
        self, name: str, value: float, *, labels: dict[str, str] | None = None
    ) -> None:
        self._validate(name, labels)
        key = self._key(name, labels)
        self._gauges[key] = float(value)

    def _validate(self, name: str, labels: dict[str, str] | None) -> None:
        if name not in METRIC_TYPES:
            raise ValueError("Unknown metric: " + name)
        for key in (labels or {}):
            if key not in ALLOWED_LABELS:
                raise ValueError("Metric label not allowed: " + key)
            if key in HIGH_CARDINALITY_FIELDS:
                raise ValueError("High-cardinality label: " + key)

    @staticmethod
    def _key(
        name: str, labels: dict[str, str] | None
    ) -> tuple[str, frozenset[tuple[str, str]]]:
        frozen = frozenset((labels or {}).items())
        return (name, frozen)

    @staticmethod
    def _percentile(sorted_values: list[float], q: float) -> float:
        """线性插值分位数（与统计惯例一致）。"""
        if not sorted_values:
            return 0.0
        if len(sorted_values) == 1:
            return sorted_values[0]
        position = q * (len(sorted_values) - 1)
        lower = int(position)
        upper = lower + 1
        if upper >= len(sorted_values):
            return sorted_values[-1]
        weight = position - lower
        return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight

    def snapshot(self) -> dict[str, object]:
        """聚合后的指标快照（按名字，不带 label 细分）。"""
        counters: dict[str, int] = {}
        histograms: dict[str, dict[str, object]] = {}
        for (name, _labels), value in self._counters.items():
            counters[name] = counters.get(name, 0) + value
        for (name, _labels), values in self._histograms.items():
            if not values:
                continue
            sorted_values = sorted(values)
            p50 = MetricRegistry._percentile(sorted_values, 0.50)
            p95 = MetricRegistry._percentile(sorted_values, 0.95)
            p99 = MetricRegistry._percentile(sorted_values, 0.99)
            histograms[name] = {
                "count": len(values),
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "p99_ms": round(p99, 2),
                "max_ms": round(sorted_values[-1], 2),
            }
        gauges: dict[str, float] = {}
        for (name, _labels), value in self._gauges.items():
            gauges[name] = value
        return {
            "counters": counters,
            "histograms": histograms,
            "gauges": gauges,
        }

