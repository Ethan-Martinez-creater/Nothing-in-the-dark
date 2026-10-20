"""Local audit artifacts for the frozen ten-task study; no fabricated human scores."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Any


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_run(report: Any, payload: dict[str, Any], output: Path) -> None:
    from app.evaluation.agent_evaluators import EVALUATOR_VERSION
    from app.evaluation.answer_delivery import ANSWER_DELIVERY_VERSION
    from app.evaluation.investigation_diagnostics import DIAGNOSTICS_VERSION

    suite_root = (
        Path(__file__).resolve().parents[2]
        / "tests/fixtures/agent_eval/investigation_real_eval_v1"
    )
    trace_dir = output.with_name(output.stem + "-traces")
    trace_dir.mkdir(parents=True, exist_ok=True)
    files = {output.name: _digest(output)}
    for task in report.task_results:
        target = trace_dir / f"{task.task_id}.json"
        if target.exists():
            raise ValueError("Trace already exists; use a new attempt output path")
        target.write_text(
            json.dumps(task.trace_bundle, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        files[str(target.relative_to(output.parent))] = _digest(target)
    frozen = {
        str(path.relative_to(suite_root)): _digest(path)
        for path in sorted(suite_root.rglob("*.json"))
    }
    policies = {
        task.task_id: task.trace_bundle.get("execution_policy") for task in report.task_results
    }
    manifest = {
        "schema_version": "investigation-run-v1",
        "evaluator_version": EVALUATOR_VERSION,
        "diagnostics_version": DIAGNOSTICS_VERSION,
        "answer_delivery_version": ANSWER_DELIVERY_VERSION,
        "finalization_policy": {
            "version": "bounded-synthesis-v1", "read_only_only": True,
            "requires_successful_tool_observation": True, "max_attempts_per_run": 1,
            "timeout_seconds": 15, "tools_enabled": False, "all_failed_reads": "runtime_refusal",
        },
        "execution_policies": policies,
        "execution_policies_sha256": hashlib.sha256(
            json.dumps(policies, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "suite": report.suite_version,
        "mode": report.mode,
        "real_model_status": payload["real_model_status"],
        "candidate_label": report.candidate_label,
        "git_sha": report.git_sha,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "sample_size": report.sample_size,
        "model_config": payload.get("model_config"),
        "tool_schema_hash": report.tool_schema_hash,
        "coordinator_prompt_hash": report.coordinator_prompt_hash,
        "frozen_files_sha256": frozen,
        "artifact_files_sha256": files,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "argv": sys.argv[1:],
        "manual_adjudication": "PENDING",
        "limitations": [
            "Contract results do not measure real-model semantic quality.",
            "Known source IDs do not alone prove claim support; manual audit required.",
            "Historical production price table is not verified for the fresh provider.",
            "Preserved task SQLite databases and raw artifacts remain local/ignored.",
            "Ten selected scenarios cannot estimate a production population success rate.",
        ],
    }
    output.with_name(output.stem + "-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
