"""Evidence-only attribution: keep provider, tool service and task timeout distinct."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

DIAGNOSTICS_VERSION = "investigation-diagnostics-v1"
POLICY_EVENTS = frozenset({
    "tool_budget_reserved", "tool_execution_blocked", "agent_execution_stopped",
    "tool_execution_start", "tool_execution_end",
})


async def collect_policy_audit(repository: Any, root_run_id: str) -> list[dict[str, Any]]:
    """Only export execution/policy audit fields, never model/tool input bodies."""
    pending = [root_run_id]
    seen: set[str] = set()
    audit: list[dict[str, Any]] = []
    while pending:
        run_id = pending.pop(0)
        if run_id in seen:
            continue
        seen.add(run_id)
        for event in await repository.list_run_events(run_id):
            if event.event_type not in POLICY_EVENTS:
                continue
            payload = {
                key: value for key, value in (event.payload or {}).items()
                if key not in {"arguments", "output", "content", "messages", "instructions"}
                and (value is None or isinstance(value, (str, bool, int, float)))
            }
            audit.append({**payload, "run_id": run_id, "event_id": event.id,
                          "event_type": event.event_type})
        pending.extend(child.id for child in await repository.list_child_runs(run_id))
    return audit


def diagnose_execution(
    *,
    status: str,
    runner_error: str | None,
    run_error_code: str | None,
    final_answer: str,
    tool_calls: Sequence[Any],
    observations: Sequence[Mapping[str, Any]],
    model_measurements: Sequence[Mapping[str, Any]],
    policy_audit: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Describe observed failures without guessing why the Agent timed out.

    No exception bodies, model messages or request URLs are copied. A timeout
    can coexist with healthy provider calls or with an unavailable embedding
    service; neither coexistence establishes a causal explanation.
    """
    invocation_failures: Counter[str] = Counter()
    failed_tools: Counter[str] = Counter()
    for call in tool_calls:
        code = getattr(call, "error_code", None)
        if code:
            invocation_failures[str(code)] += 1
            failed_tools[str(getattr(call, "tool", "unknown"))] += 1

    business_failures: list[dict[str, str]] = []
    for item in observations:
        result = item.get("result")
        if isinstance(result, Mapping) and result.get("ok") is False:
            error = result.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            business_failures.append({
                "tool": str(item.get("tool") or "unknown"),
                "error_code": str(code or "business_result_not_ok"),
            })

    provider_failures = [
        {
            "request_index": index,
            "error_code": item.get("error_code"),
            "route": item.get("route"),
            "duration_ms": item.get("duration_ms"),
            "usage_known": item.get("input_tokens") is not None
            and item.get("output_tokens") is not None,
        }
        for index, item in enumerate(model_measurements, 1)
        if item.get("status") != "succeeded"
    ]
    timed_out = bool(runner_error and runner_error.startswith("TimeoutError:"))
    layers: list[str] = []
    if invocation_failures.get("embedding_worker_unavailable"):
        layers.append("embedding_service_unavailable")
    if any(code.startswith("eval_injected_") for code in invocation_failures):
        layers.append("controlled_tool_fault")
    if provider_failures:
        layers.append("model_provider_error")
    if timed_out:
        layers.append("agent_execution_timeout")
    if business_failures:
        layers.append("tool_business_result_unavailable")
    stop_reasons = sorted({
        str(event.get("reason")) for event in policy_audit
        if event.get("event_type") == "agent_execution_stopped" and event.get("reason")
    })
    if run_error_code in {"tool_budget_exhausted", "agent_turn_budget_exhausted"} or any(
        reason in {"tool_budget_exhausted", "agent_turn_budget_exhausted"}
        for reason in stop_reasons
    ):
        layers.append("runtime_budget_stop")
    return {
        "version": DIAGNOSTICS_VERSION,
        "observed_layers": layers,
        "agent": {
            "status": status,
            "coordinator_completed": status == "completed",
            "deadline_exceeded": timed_out,
            "run_error_code": run_error_code,
            "runtime_stop_reasons": stop_reasons,
            "has_recorded_answer_content": bool(final_answer.strip()),
            "answer_may_be_partial": bool(final_answer.strip())
            and (status != "completed" or bool(stop_reasons)),
        },
        "tools": {
            "failed_invocations_by_error": dict(sorted(invocation_failures.items())),
            "failed_invocations_by_tool": dict(sorted(failed_tools.items())),
            "business_failures": business_failures,
        },
        "provider": {
            "observed_request_count": len(model_measurements),
            "failed_measurements": provider_failures,
            "cancelled_inflight_usage": "NOT_MEASURABLE",
        },
        "causal_attribution": "NOT_ESTABLISHED",
        "interpretation": (
            "Observed layers are separate facts, not proof that any single layer caused "
            "the task failure. A retained expert answer does not prove coordinator completion."
        ),
    }
