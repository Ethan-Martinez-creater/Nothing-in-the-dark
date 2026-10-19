"""Bounded serial orchestration of the existing CLI, preserving each attempt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.evaluation.agent_dataset import load_suite

SUITE = "investigation_real_eval_v1"


def run(args: argparse.Namespace) -> int:
    suite = load_suite(suite_version=SUITE)
    ids = args.tasks.split(",") if args.tasks else [task.id for task in suite.tasks]
    if not ids or len(ids) > 10 or len(ids) != len(set(ids)):
        raise ValueError("Choose 1–10 distinct frozen tasks per bounded attempt")
    tasks = [suite.by_id(task_id) for task_id in ids]
    output = Path(args.output_dir).resolve()
    if output.exists():
        raise ValueError("Preserve old attempts; choose a new output directory")
    output.mkdir(parents=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path.cwd())
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    temp = Path("artifacts/agent_eval/real_v1/temp").resolve()
    temp.mkdir(parents=True, exist_ok=True)
    environment["TEMP"] = str(temp)
    environment["TMP"] = str(temp)
    index = {
        "suite": SUITE, "attempt": args.attempt_label, "mode": args.mode,
        "started_at": datetime.now(UTC).isoformat(), "status": "running",
        "selected_tasks": ids, "tasks": [], "hard_stop_reason": None,
    }

    def checkpoint() -> None:
        (output / "attempt.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    checkpoint()
    for task in tasks:
        if index["hard_stop_reason"]:
            index["tasks"].append({"id": task.id, "status": "BLOCKED_NOT_RUN"})
            checkpoint()
            continue
        target = output / f"{task.id}.json"
        command = [
            sys.executable, "-B", "-m", "app.scripts.run_agent_eval",
            "--suite-version", SUITE, "--mode", args.mode,
            "--tasks", task.id, "--candidate-label", args.attempt_label,
            "--llm-config-file", args.config_file,
            "--preflight-receipt", args.preflight_receipt,
            "--output", str(target), "--fail-on-hard-gate",
        ]
        entry = {"id": task.id, "status": "RUNNING", "command": command}
        index["tasks"].append(entry)
        checkpoint()
        print(f"{task.id}: starting {args.mode}", flush=True)
        timeout = (task.budgets.task_timeout_seconds or 240) + 180
        try:
            with (output / f"{task.id}.stdout.log").open("wb") as stdout:
                with (output / f"{task.id}.stderr.log").open("wb") as stderr:
                    result = subprocess.run(
                        command, env=environment, stdout=stdout, stderr=stderr,
                        timeout=timeout, check=False,
                    )
            entry["exit_code"] = result.returncode
            if not target.is_file():
                entry["status"] = "INFRASTRUCTURE_ERROR"
                index["hard_stop_reason"] = "CLI exited without a report; inspect retained logs"
            else:
                report = json.loads(target.read_text(encoding="utf-8"))
                record = report["task_results"][0]
                entry.update({
                    "status": record["status"],
                    "report_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "report": target.name,
                    "run_id": record["run_id"],
                    "deterministic_pass": not record["failure_details"]
                    and record["status"] in {"completed", "waiting_approval"},
                    "manual_acceptance": None,
                })
                if report.get("hard_stop_reason"):
                    index["hard_stop_reason"] = report["hard_stop_reason"]
        except subprocess.TimeoutExpired:
            entry["status"] = "INFRASTRUCTURE_TIMEOUT"
            index["hard_stop_reason"] = "CLI exceeded task plus persistence timeout"
        checkpoint()
        print(f"{task.id}: {entry['status']}", flush=True)
    index["finished_at"] = datetime.now(UTC).isoformat()
    index["status"] = "blocked" if index["hard_stop_reason"] else "completed"
    checkpoint()
    return 1 if index["hard_stop_reason"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("contract", "real_model"), default="real_model")
    parser.add_argument("--tasks", default="")
    parser.add_argument("--attempt-label", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--config-file", default=".env")
    parser.add_argument("--preflight-receipt", required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
