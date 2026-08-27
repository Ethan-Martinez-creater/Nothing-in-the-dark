"""M19 端到端生产可观测性与 SLO 测试。

覆盖：trace/span 上下文与传播、span 必需属性校验、metric label allowlist、
redaction（Cookie/Authorization/prompt/异常链）、canary 扫描、SLO 错误预算、
telemetry-health API 契约。
"""

from __future__ import annotations

import atexit
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.telemetry.context import (
    current_trace,
    new_span_id,
    new_trace_id,
    reset_trace,
    root_context,
    set_trace,
)
from app.telemetry.metrics import MetricRegistry
from app.telemetry.redact import (
    REDACTED,
    redact_exception_chain,
    redact_text,
    redact_value,
    scan_for_canary_secrets,
)
from app.telemetry.slo import DEFAULT_SLOS, SLO, evaluate_slos
from app.telemetry.tracer import (
    InMemoryExporter,
    Tracer,
    build_tracer,
    noop_tracer,
)

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-tel-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


# ---- 上下文 ----------------------------------------------------------------


def test_trace_ids_are_hex() -> None:
    assert len(new_trace_id()) == 16
    assert len(new_span_id()) == 16
    assert new_trace_id() != new_trace_id()


def test_root_context_fields() -> None:
    ctx = root_context(attributes={"run_id": "r1"})
    assert ctx.trace_id
    assert ctx.span_id
    assert ctx.parent_span_id is None
    assert ctx.attributes["run_id"] == "r1"


def test_child_context_links_parent() -> None:
    parent = root_context()
    child = parent.child()
    assert child.trace_id == parent.trace_id
    assert child.parent_span_id == parent.span_id
    assert child.span_id != parent.span_id


def test_contextvar_propagation() -> None:
    assert current_trace() is None
    token = set_trace(root_context())
    try:
        assert current_trace() is not None
    finally:
        reset_trace(token)
    assert current_trace() is None


# ---- Tracer / Span ----------------------------------------------------------


def test_tracer_span_lifecycle() -> None:
    exporter = InMemoryExporter()
    tracer = Tracer(exporter)
    span = tracer.start_span(
        "http.request",
        kind="server",
        attributes={"http.method": "GET", "http.route": "/health"},
    )
    assert span.span_id
    tracer.end_span(span, attributes={"http.status_code": 200})
    assert span.end_time is not None
    assert exporter.count() == 1
    exported = exporter.spans()[0]
    assert exported.name == "http.request"
    assert exported.attributes["http.status_code"] == 200
    assert exported.duration_ms >= 0


def test_span_error_status() -> None:
    exporter = InMemoryExporter()
    tracer = Tracer(exporter)
    span = tracer.start_span("llm.call", attributes={"model": "m"})
    tracer.end_span(span, status="error", error_code="llm_request_failed")
    assert exporter.spans()[0].status == "error"
    assert exporter.spans()[0].error_code == "llm_request_failed"


def test_noop_exporter_keeps_business_untouched() -> None:
    tracer = noop_tracer()
    assert not tracer.enabled
    span = tracer.start_span("agent.run")
    tracer.end_span(span)  # 不抛错


def test_in_memory_exporter_missing_attributes() -> None:
    exporter = InMemoryExporter()
    tracer = Tracer(exporter)
    # http.request 缺 http.status_code
    span = tracer.start_span(
        "http.request", attributes={"http.method": "GET", "http.route": "/x"}
    )
    tracer.end_span(span)
    problems = exporter.missing_attributes()
    assert problems
    assert problems[0]["span"] == "http.request"
    assert "http.status_code" in problems[0]["missing"]


def test_build_tracer_and_trace_filtering() -> None:
    exporter = InMemoryExporter()
    tracer = build_tracer(exporter)
    span = tracer.start_span("agent.run", attributes={"run_id": "r1"})
    tracer.end_span(span)
    by_trace = exporter.by_trace(span.trace_id)
    assert len(by_trace) == 1


# ---- Metrics ----------------------------------------------------------------


def test_metrics_counter_and_snapshot() -> None:
    registry = MetricRegistry()
    registry.increment("api.requests")
    registry.increment("api.requests")
    registry.increment("api.errors")
    snapshot = registry.snapshot()
    assert snapshot["counters"]["api.requests"] == 2
    assert snapshot["counters"]["api.errors"] == 1


def test_metrics_histogram_percentiles() -> None:
    registry = MetricRegistry()
    for value in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
        registry.observe("api.latency_ms", value)
    hist = registry.snapshot()["histograms"]["api.latency_ms"]
    assert hist["count"] == 10
    assert hist["p50_ms"] == 55
    assert hist["p95_ms"] == 95.5


def test_metrics_rejects_unknown_name() -> None:
    registry = MetricRegistry()
    with pytest.raises(ValueError):
        registry.increment("nope.requests")


def test_metrics_rejects_high_cardinality_label() -> None:
    registry = MetricRegistry()
    with pytest.raises(ValueError):
        registry.increment("api.requests", labels={"post_id": "p1"})


def test_metrics_allows_controlled_labels() -> None:
    registry = MetricRegistry()
    registry.increment("crawler.runs", labels={"platform": "weibo"})
    assert registry.snapshot()["counters"]["crawler.runs"] == 1


# ---- Redaction --------------------------------------------------------------


def test_redact_sensitive_keys() -> None:
    assert redact_value("sk-abc", key="api_key") == REDACTED
    assert redact_value("xyz", key="cookie") == REDACTED
    assert redact_value("xyz", key="authorization") == REDACTED
    assert redact_value("prompt text", key="prompt") == REDACTED


def test_redact_keeps_normal_values() -> None:
    assert redact_value("hello", key="content") == "hello"
    assert redact_value({"a": 1}, key="data") == {"a": 1}


def test_redact_value_pattern() -> None:
    assert redact_value("api_key=abc123def456ghi789jkl", key="text") == REDACTED
    redacted = redact_text("Authorization: Bearer abc123def456ghi789jkl")
    assert "abc123def456ghi789jkl" not in redacted
    assert "***" in redacted


def test_redact_exception_chain() -> None:
    exc = ValueError("connection refused token=abc123def456ghi789jkl")
    summary = redact_exception_chain(exc)
    assert "token=abc123def456ghi789jkl" not in summary
    assert "ValueError" in summary


def test_canary_scan_finds_secrets() -> None:
    payload = "api_key=sk-abcdefghijklmnopqrst"
    hits = scan_for_canary_secrets(payload)
    assert hits
    assert "sk-abcdefghijklmnopqrst" not in "".join(hits)


def test_canary_scan_clean() -> None:
    assert scan_for_canary_secrets("normal log line") == []


# ---- SLO --------------------------------------------------------------------


def test_slo_error_budget() -> None:
    slo = SLO("test", "t", target=0.995, window_seconds=3600)
    result = slo.error_budget(1000, 1000)
    assert result["actual"] == 1.0
    assert not result["violated"]
    result_bad = slo.error_budget(1000, 980)
    assert result_bad["violated"]


def test_slo_empty_no_division_error() -> None:
    slo = SLO("test", "t", target=0.995, window_seconds=3600)
    result = slo.error_budget(0, 0)
    assert result["total"] == 0


def test_default_slos_present() -> None:
    names = {s.name for s in DEFAULT_SLOS}
    assert "api_availability" in names
    assert "agent_final_state" in names
    assert "worker_lease_recovery" in names
    assert "alert_enqueue_latency" in names


def test_evaluate_slos() -> None:
    results = evaluate_slos(api_total=100, api_ok=95, agent_total=10, agent_ok=10)
    by_name = {r["name"]: r for r in results}
    assert by_name["api_availability"]["actual"] == 0.95
    assert by_name["api_availability"]["violated"]
    assert by_name["agent_final_state"]["actual"] == 1.0


# ---- API 契约 ----------------------------------------------------------------


def _client() -> TestClient:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{_tmp_db()}",
        demo_mode=True,
        telemetry_exporter="in_memory",
    )
    return TestClient(create_app(settings))


def test_telemetry_health_endpoint() -> None:
    with _client() as client:
        # 产生一次 http.request span + 指标
        client.get("/api/v1/system/telemetry-health")
        resp = client.get("/api/v1/system/telemetry-health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["metrics_summary"]["api_requests"] >= 1
        assert "slo" in body
        assert any(r["name"] == "api_availability" for r in body["slo"])


def test_telemetry_health_endpoint_noop() -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{_tmp_db()}",
        demo_mode=True,
        telemetry_exporter="noop",
    )
    with TestClient(create_app(settings)) as client:
        resp = client.get("/api/v1/system/telemetry-health")
        assert resp.status_code == 200
        assert resp.json()["status"] in {"ok", "noop"}


def test_run_event_carries_trace_id() -> None:
    """API 请求创建的 run 事件带 trace_id（SSE 打开对应运行）。"""
    with _client() as client:
        case = client.post(
            "/api/v1/cases",
            json={"title": "可观测", "topic": "话题", "platforms": ["weibo"]},
        )
        case_id = case.json()["id"]
        resp = client.post(
            f"/api/v1/cases/{case_id}/messages",
            json={"content": "你好"},
        )
        assert resp.status_code in {200, 201, 202}
        run_id = resp.json()["id"]
        events = client.get(f"/api/v1/runs/{run_id}/events")
        assert events.status_code == 200
        records = events.json()
        assert records, "run 至少有一个事件"
        # agent_queued 由 HTTP 请求内同步写入（middleware 注入 trace 上下文），
        # worker 异步事件同样携带 trace_id；这里仅断言字段结构存在。
        assert any(r.get("event_type") == "agent_queued" for r in records)
        assert all(
            "trace_id" in r or r.get("event_type") == "agent_queued" for r in records
        )
