"""Offline E6/E13 audit of retained traces. Never invokes a model or rewrites a run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from app.evaluation.agent_dataset import load_suite
from app.evaluation.agent_evaluators import (
    EvalContext,
    evaluate_citation_validity,
    evaluate_controlled_read_fault,
)
from app.evaluation.agent_trace import AgentTrace, CaseStateSnapshot, StateDiff, ToolCallView
from app.evaluation.investigation_diagnostics import diagnose_execution


def repair_archived_refs(trace: dict, task: Any, fixture: dict) -> tuple[dict, set[str]]:
    """Confirm missing typed IDs using frozen identities + successful observations.

    The answer is deliberately not read. Merely appearing in a tool result is
    insufficient: comments match seeded post/native/content, reviews reference
    a seeded reviewable Finding, signals match the frozen detector identity.
    """
    refs = dict(trace["ref_map"])
    cases = {k.removeprefix("case:"): v for k, v in refs.items() if k.startswith("case:")}
    allowed_cases = {task.case_id}
    if "cross_investigation" in task.tags:
        for link in fixture.get("cross_links", []):
            pair = {link["left_case_key"], link["right_case_key"]}
            if task.case_id in pair:
                allowed_cases.update(pair)
    owned_keys: set[str] = set()
    comments: dict[tuple[str, str], str] = {}
    reviewable: dict[str, str] = {}
    for inv in fixture["investigations"]:
        if inv["key"] not in allowed_cases:
            continue
        owned_keys.add("case:" + inv["key"])
        for category, plural in (
            ("post", "posts"), ("account", "accounts"), ("claim", "claims"),
            ("evidence", "evidence"), ("finding", "findings"), ("media", "media"),
        ):
            owned_keys.update(f"{category}:{item['key']}" for item in inv.get(plural, []))
        owned_keys.update({"report:" + inv["key"], "artifact:report:" + inv["key"]})
        for post in inv.get("posts", []):
            post_id = refs.get("post:" + post["key"])
            for comment in post.get("comments", []):
                comments[(post_id, comment["native_id"])] = comment.get("content", "")
        for review in inv.get("review_items", []):
            key = review.get("object_id_key") if review.get("object_type") == "finding" else None
            if key and refs.get("finding:" + key):
                reviewable[refs["finding:" + key]] = key
    for entity in fixture.get("workspace_entities", []):
        if allowed_cases.intersection(entity.get("case_keys", [])):
            owned_keys.add("entity:" + entity["key"])
    permitted = {value for key, value in refs.items() if key in owned_keys}
    allowed_runtime_cases = {cases[key] for key in allowed_cases}

    def add(key: str, value: Any) -> None:
        try:
            if str(UUID(str(value))) != str(value).lower():
                return
        except ValueError:
            return
        refs[key] = str(value)
        permitted.add(str(value))

    for observation in trace.get("observations", []):
        if observation.get("status") != "succeeded":
            continue
        if observation.get("arguments", {}).get("case_id") not in allowed_runtime_cases:
            continue
        result = observation.get("result")
        if not isinstance(result, dict) or result.get("ok") is False:
            continue
        tool = observation["tool"]
        if tool == "query_social_comments":
            for comment in result.get("comments", []):
                identity = (comment.get("post_id"), comment.get("native_id"))
                if identity in comments and comment.get("content") == comments[identity]:
                    add("comment:" + str(comment["native_id"]), comment.get("id"))
        elif tool == "query_review_items":
            for review in result.get("review_items", []):
                if review.get("object_type") == "finding" and review.get("object_id") in reviewable:
                    add("review_item:" + reviewable[review["object_id"]], review.get("id"))
        elif tool == "query_signals":
            for signal in result.get("signals", []):
                for seed in fixture.get("signals", []):
                    if seed["case_key"] not in allowed_cases:
                        continue
                    expected_cases = {cases[seed["case_key"]]} | {
                        cases[key] for key in seed.get("related_case_keys", [])
                    }
                    if (
                        all(signal.get(key) == seed.get(key) for key in (
                            "signal_type", "title", "detector_version"
                        ))
                        and set(signal.get("related_case_ids", [])) == expected_cases
                    ):
                        add("signal:" + seed["fingerprint"], signal.get("id"))
    return refs, permitted


def audit_report(path: Path, suite: Any, task_id: str) -> dict:
    source_bytes = path.read_bytes()
    raw = json.loads(source_bytes)
    record = next(item for item in raw["task_results"] if item["task_id"] == task_id)
    task = suite.by_id(task_id)
    bundle = record["trace_bundle"]
    refs, permitted = repair_archived_refs(bundle, task, suite.fixtures[task.fixture_id])
    def calls(key: str) -> tuple[ToolCallView, ...]:
        return tuple(ToolCallView(**item) for item in bundle.get(key, []))

    trace = AgentTrace(
        run_id=bundle["run_id"], case_id=bundle["case_id"], status=bundle["status"],
        objective=bundle["objective"], final_answer=bundle["final_answer"],
        tool_calls=calls("tool_calls"), child_tool_calls=calls("child_tool_calls"),
    )
    # E6/E13 do not inspect state; no state-based acceptance is recalculated.
    snapshot = CaseStateSnapshot(case_id=trace.case_id)
    ctx = EvalContext(
        task=task, trace=trace, state_before=snapshot, state_after=snapshot,
        state_diff=StateDiff(), known_ids={"verified_archived_refs": set(refs.values())},
        ref_map=refs, case_known_ids={task.case_id: permitted},
    )
    outcomes = [evaluate_citation_validity(ctx), evaluate_controlled_read_fault(ctx)]
    from app.evaluation.agent_evaluators import EVALUATOR_VERSION

    assert path.read_bytes() == source_bytes, "Source artifact changed during offline audit"
    return {
        "kind": "EVALUATOR_ONLY_NO_MODEL_RERUN", "task_id": task_id,
        "source_attempt": path.parent.name, "run_id": trace.run_id,
        "source_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "evaluator_version": EVALUATOR_VERSION,
        "confirmed_added_refs": {key: value for key, value in refs.items()
                                 if key not in bundle["ref_map"]},
        "original_outcomes": [item for item in record["outcomes"]
                              if item["evaluator"].startswith(("E6_", "E13_"))],
        "repaired_outcomes": [item.to_dict() for item in outcomes],
        "original_status": record["status"],
        "retained_trace_diagnostics": diagnose_execution(
            status=trace.status, runner_error=record.get("error"), run_error_code=None,
            final_answer=trace.final_answer,
            tool_calls=tuple(trace.tool_calls) + tuple(trace.child_tool_calls),
            observations=bundle.get("observations", []),
            model_measurements=record.get("model_measurements", []),
        ),
        "baseline_or_manual_acceptance_recalculated": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-dir", required=True, type=Path)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Retain previous audit; choose a new output")
    suite = load_suite(suite_version="investigation_real_eval_v1")
    audits = [audit_report(args.attempt_dir / f"{key}.json", suite, key)
              for key in args.tasks.split(",")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Retained-trace evaluator audit: {len(audits)} cases; no model calls")


if __name__ == "__main__":
    main()
