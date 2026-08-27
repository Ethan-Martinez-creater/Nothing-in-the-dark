"""M20 真实数据评测、回归门禁与在线质量监控测试。

覆盖：manifest 校验（hash/重复 ID/泄漏/缺许可）、evaluator registry
（崩溃隔离）、门禁（绝对阈值/相对回归/样本不足/安全指标不可豁免）、
bootstrap 置信区间、PSI/JS 漂移、API 契约。
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
from app.services.evaluation import (
    EvaluatorDefinition,
    EvaluatorRegistry,
    build_default_registry,
    classification_report,
)
from app.services.quality_gate import (
    GATE_BLOCK,
    GATE_INCONCLUSIVE,
    GATE_PASS,
    DatasetManifest,
    QualityGateError,
    ReleaseGate,
    bootstrap_ci,
    content_hash,
    drift_alert,
    js_divergence,
    psi_divergence,
    validate_examples,
)

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-eval-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


# ---- manifest 治理 -----------------------------------------------------------


def test_manifest_validates_required_fields() -> None:
    manifest = DatasetManifest(name="", version="1.0", task="sentiment")
    with pytest.raises(QualityGateError):
        manifest.validate()


def test_manifest_requires_license() -> None:
    manifest = DatasetManifest(
        name="d", version="1.0", task="sentiment", source="s", license=""
    )
    with pytest.raises(QualityGateError):
        manifest.validate()


def test_manifest_hash_stable_and_content_addressed() -> None:
    manifest = DatasetManifest(
        name="d", version="1.0", task="sentiment", license="research"
    )
    examples = [{"example_id": "e1", "gold": {"label": "positive"}}]
    h1 = manifest.manifest_hash(examples)
    h2 = manifest.manifest_hash(examples)
    assert h1 == h2
    assert len(h1) == 64
    changed = manifest.manifest_hash(
        [{"example_id": "e1", "gold": {"label": "negative"}}]
    )
    assert changed != h1


def test_validate_examples_detects_duplicates_and_leaks() -> None:
    problems = validate_examples(
        [
            {"example_id": "a", "gold": {"x": 1}},
            {"example_id": "a", "gold": {"x": 2}},
            {"example_id": "b", "gold": {"x": 3}, "input_hash": "abc"},
            {"example_id": "c", "gold": {"x": 4}, "input_hash": "abc"},
        ]
    )
    assert any("duplicate" in p for p in problems)
    assert any("leak" in p for p in problems)


def test_content_hash() -> None:
    assert content_hash({"a": 1}) == content_hash({"a": 1})
    assert content_hash({"a": 1}) != content_hash({"a": 2})


# ---- evaluator registry ------------------------------------------------------


def test_registry_run_suite() -> None:
    registry = EvaluatorRegistry()
    registry.register(
        EvaluatorDefinition(
            "double",
            "value",
            lambda examples, config: {"value": len(examples) * 2},
        )
    )
    outcome = registry.run_suite(["double"], [{"x": 1}, {"x": 2}])
    assert outcome["all_passed"]
    assert outcome["results"]["double"]["value"] == 4


def test_registry_isolates_evaluator_crash() -> None:
    registry = EvaluatorRegistry()

    def broken(_examples, _config):
        raise RuntimeError("boom")

    registry.register(EvaluatorDefinition("broken", "m", broken))
    registry.register(
        EvaluatorDefinition("ok", "m", lambda e, c: {"value": 1})
    )
    outcome = registry.run_suite(["broken", "ok"], [])
    assert not outcome["all_passed"]
    assert outcome["failed"][0]["evaluator"] == "broken"
    assert outcome["results"]["ok"]["value"] == 1


def test_build_default_registry() -> None:
    registry = build_default_registry()
    assert "sentiment" in registry.names()
    assert "stance" in registry.names()
    assert "propagation_edges" in registry.names()
    assert "claim_citations" in registry.names()


def test_classification_report_handles_empty() -> None:
    report = classification_report([], [], ["a", "b"])
    assert report["accuracy"] == 0.0


# ---- release gates -----------------------------------------------------------


def test_gate_absolute_threshold() -> None:
    gate = ReleaseGate("g", thresholds={"citation_correctness": 0.98})
    outcome = gate.evaluate({"citation_correctness": 0.99})
    assert outcome["decision"] == GATE_PASS
    outcome_bad = gate.evaluate({"citation_correctness": 0.90})
    assert outcome_bad["decision"] == GATE_BLOCK
    assert outcome_bad["violations"][0]["kind"] == "absolute"


def test_gate_relative_regression() -> None:
    gate = ReleaseGate(
        "g",
        relative_regression_limits={"macro_f1": 0.02},
    )
    outcome = gate.evaluate(
        {"macro_f1": 0.80},
        baseline={"macro_f1": 0.85},
    )
    assert outcome["decision"] == GATE_BLOCK
    assert outcome["violations"][0]["kind"] == "relative_regression"


def test_gate_insufficient_sample() -> None:
    gate = ReleaseGate("g", thresholds={"macro_f1": 0.5})
    outcome = gate.evaluate(
        {"macro_f1": 0.9},
        sample_sizes={"macro_f1": 5},
    )
    assert outcome["decision"] == GATE_BLOCK
    assert any(v["kind"] == "insufficient_sample" for v in outcome["violations"])


def test_gate_disabled_is_inconclusive() -> None:
    gate = ReleaseGate("g", enabled=False)
    assert gate.evaluate({"x": 1})["decision"] == GATE_INCONCLUSIVE


def test_security_metrics_cannot_be_exempted() -> None:
    gate = ReleaseGate("g", thresholds={"attack_success_rate": 0.0})
    outcome = gate.evaluate({"attack_success_rate": 0.1})
    assert outcome["decision"] == GATE_BLOCK


def test_bootstrap_ci() -> None:
    ci = bootstrap_ci([0.1, 0.2, -0.1, 0.3, 0.0, 0.15, -0.05, 0.25])
    assert ci["n"] == 8
    assert "ci_low" in ci
    assert "ci_high" in ci
    assert bootstrap_ci([])["n"] == 0


# ---- 在线漂移 ----------------------------------------------------------------


def test_psi_divergence() -> None:
    psi = psi_divergence({"a": 0.5, "b": 0.5}, {"a": 0.9, "b": 0.1})
    assert psi > 0
    assert psi_divergence({"a": 0.5}, {"a": 0.5}) == 0


def test_js_divergence_bounds() -> None:
    js = js_divergence({"a": 1.0}, {"b": 1.0})
    assert js > 0
    assert js <= 1.0


def test_drift_alert_is_signal_only() -> None:
    result = drift_alert(
        baseline={"pos": 0.5, "neg": 0.5},
        current={"pos": 0.95, "neg": 0.05},
    )
    assert result["drifted"]
    assert result["signal_only"]
    stable = drift_alert(
        baseline={"pos": 0.5, "neg": 0.5},
        current={"pos": 0.5, "neg": 0.5},
    )
    assert not stable["drifted"]


# ---- API 契约 ----------------------------------------------------------------


def _client() -> TestClient:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{_tmp_db()}",
        demo_mode=True,
    )
    return TestClient(create_app(settings))


def test_dataset_register_and_list_api() -> None:
    with _client() as client:
        resp = client.post(
            "/api/v1/system/evaluation/datasets",
            json={
                "manifest": {
                    "name": "sentiment-dev",
                    "version": "1.0.0",
                    "task": "sentiment",
                    "source": "internal",
                    "license": "research-only",
                    "schema_version": "1.0",
                },
                "examples": [
                    {"example_id": "e1", "input": "这是好评", "gold": "positive"},
                    {"example_id": "e2", "input": "这是差评", "gold": "negative"},
                ],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["example_count"] == 2
        assert len(body["content_hash"]) == 64
        listing = client.get("/api/v1/system/evaluation/datasets")
        assert listing.status_code == 200
        assert any(d["name"] == "sentiment-dev" for d in listing.json())


def test_dataset_rejects_invalid_manifest() -> None:
    with _client() as client:
        resp = client.post(
            "/api/v1/system/evaluation/datasets",
            json={"manifest": {"name": "x"}, "examples": []},
        )
        assert resp.status_code == 400


def test_evaluation_run_and_gates_api() -> None:
    with _client() as client:
        # 注册数据集
        ds = client.post(
            "/api/v1/system/evaluation/datasets",
            json={
                "manifest": {
                    "name": "sentiment-run",
                    "version": "1.0.0",
                    "task": "sentiment",
                    "source": "internal",
                    "license": "research-only",
                    "schema_version": "1.0",
                },
                "examples": [
                    {"example_id": "e1", "input": "这是好评", "gold": "positive"},
                    {"example_id": "e2", "input": "这是差评", "gold": "negative"},
                ],
            },
        )
        manifest_id = ds.json()["manifest"]["id"]
        # 创建门禁
        gate = client.post(
            "/api/v1/system/evaluation/gates",
            json={
                "name": "sentiment_gate",
                "suite": "default",
                "thresholds": {"sentiment.macro_f1": 0.0},
                "relative_regression_limits": {},
                "mandatory": True,
            },
        )
        assert gate.status_code == 200
        # 运行评测
        run = client.post(
            "/api/v1/system/evaluation/runs",
            json={
                "suite": "default",
                "candidate_version": "c1",
                "baseline_version": "b1",
                "dataset_manifest_id": manifest_id,
                "evaluator_names": ["sentiment"],
            },
        )
        assert run.status_code == 200
        run_body = run.json()
        assert run_body["run"]["status"] == "completed"
        assert run_body["aggregate"]["sentiment.macro_f1"] > 0.0
        run_id = run_body["run"]["id"]
        # 门禁判定
        gates = client.post(f"/api/v1/system/evaluation/runs/{run_id}:gates", json={})
        assert gates.status_code == 200
        results = gates.json()["gate_results"]
        assert any(r["gate_name"] == "sentiment_gate" for r in results)
        # 运行详情
        detail = client.get(f"/api/v1/system/evaluation/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["aggregate"]


def test_drift_api() -> None:
    with _client() as client:
        resp = client.post(
            "/api/v1/system/evaluation/drift",
            json={
                "baseline": {"pos": 0.5, "neg": 0.5},
                "current": {"pos": 0.95, "neg": 0.05},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["drifted"]
        assert resp.json()["signal_only"]
