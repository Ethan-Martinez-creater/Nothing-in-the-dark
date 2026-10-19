from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.scripts import run_investigation_study as study


def _args(output):
    return argparse.Namespace(
        mode="real_model", tasks="IR01,IR05", output_dir=str(output),
        attempt_label="offline-test", config_file=".env",
        preflight_receipt="not-used-by-fake-process.json",
    )


def test_provider_hard_stop_checkpoints_first_failure_and_never_starts_next(tmp_path, monkeypatch):
    calls = []

    def fake_cli(command, **_kwargs):
        calls.append(command)
        target = Path(command[command.index("--output") + 1])
        target.write_text(json.dumps({
            "hard_stop_reason": "provider_auth_or_permission_error",
            "task_results": [{"run_id": "fake-run", "status": "failed",
                              "failure_details": [{"evaluator": "provider_error"}]}],
        }), encoding="utf-8")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(study.subprocess, "run", fake_cli)
    output = tmp_path / "attempt"
    assert study.run(_args(output)) == 1
    saved = json.loads((output / "attempt.json").read_text(encoding="utf-8"))
    assert len(calls) == 1
    assert saved["status"] == "blocked"
    assert saved["tasks"][0]["status"] == "failed"
    assert saved["tasks"][0]["manual_acceptance"] is None
    assert saved["tasks"][1]["status"] == "BLOCKED_NOT_RUN"
    assert (output / "IR01.json").is_file()


def test_existing_attempt_is_preserved_without_executing_any_cli(tmp_path, monkeypatch):
    marker = tmp_path / "attempt.json"
    marker.write_text("old-evidence", encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        pytest.fail("Existing attempt must not start another request")

    monkeypatch.setattr(study.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="Preserve old attempts"):
        study.run(_args(tmp_path))
    assert marker.read_text(encoding="utf-8") == "old-evidence"
