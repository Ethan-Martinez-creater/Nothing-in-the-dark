from types import SimpleNamespace

import pytest

from app.evaluation.agent_eval import AgentSuiteRunner
from app.evaluation.investigation_diagnostics import collect_policy_audit, diagnose_execution


def _diagnose(**overrides):
    values = dict(
        status="completed", runner_error=None, run_error_code=None,
        final_answer="answer", tool_calls=(), observations=(), model_measurements=(),
    )
    values.update(overrides)
    return diagnose_execution(**values)


def test_provider_tool_and_agent_timeout_coexist_without_single_cause():
    result = _diagnose(
        status="cancelled", runner_error="TimeoutError: deadline reached",
        final_answer="expert partial graph",
        tool_calls=(SimpleNamespace(
            tool="search_social_evidence", error_code="embedding_worker_unavailable"
        ),),
        model_measurements=(
            {"status": "succeeded", "input_tokens": 10, "output_tokens": 2},
            {"status": "failed", "error_code": "APIConnectionError", "route": "fast",
             "duration_ms": 44953.0, "input_tokens": None, "output_tokens": None,
             "exception_body": "secret provider URL must not be exported"},
        ),
    )
    assert result["observed_layers"] == [
        "embedding_service_unavailable", "model_provider_error", "agent_execution_timeout"
    ]
    assert result["causal_attribution"] == "NOT_ESTABLISHED"
    assert result["agent"]["answer_may_be_partial"] is True
    assert result["agent"]["coordinator_completed"] is False
    assert result["provider"]["failed_measurements"][0]["usage_known"] is False
    assert "secret provider" not in str(result)


def test_timeout_without_provider_error_does_not_invent_provider_cause():
    result = _diagnose(
        status="cancelled", runner_error="TimeoutError: task deadline", final_answer="",
        model_measurements=({"status": "succeeded", "input_tokens": 5, "output_tokens": 3},),
    )
    assert result["observed_layers"] == ["agent_execution_timeout"]
    assert result["provider"]["failed_measurements"] == []


def test_business_unavailability_is_distinct_from_failed_invocation():
    result = _diagnose(observations=(
        {"tool": "get_collection_run", "status": "succeeded", "result": {
            "ok": False, "error": {"code": "collection_run_unavailable"}
        }},
    ))
    assert result["observed_layers"] == ["tool_business_result_unavailable"]
    assert result["tools"]["failed_invocations_by_error"] == {}
    assert result["tools"]["business_failures"][0]["error_code"] == "collection_run_unavailable"


def test_controlled_fault_and_budget_are_not_model_provider_errors():
    result = _diagnose(
        run_error_code="tool_budget_exhausted",
        tool_calls=(SimpleNamespace(
            tool="query_social_posts", error_code="eval_injected_persistent_read"
        ),),
    )
    assert result["observed_layers"] == ["controlled_tool_fault", "runtime_budget_stop"]
    assert result["provider"]["failed_measurements"] == []


def test_explicit_completed_budget_stop_is_not_a_timeout():
    result = _diagnose(policy_audit=(
        {"event_type": "agent_execution_stopped", "reason": "tool_budget_exhausted"},
    ))
    assert result["observed_layers"] == ["runtime_budget_stop"]
    assert result["agent"]["deadline_exceeded"] is False
    assert result["agent"]["answer_may_be_partial"] is True


def test_cancelled_finalization_request_does_not_invent_provider_error():
    result = _diagnose(model_measurements=(
        {"status": "cancelled", "error_code": "CancelledError", "duration_ms": 15000,
         "input_tokens": None, "output_tokens": None},
    ))
    assert result["observed_layers"] == ["model_request_cancelled"]
    assert result["provider"]["failed_measurements"] == []
    assert result["provider"]["cancelled_measurements"][0]["usage_known"] is False


def test_citation_scope_excludes_near_names_but_allows_declared_cross_links():
    task = SimpleNamespace(
        suite_version="investigation_real_eval_v1", case_id="a", tags=("read_only",)
    )
    seed = SimpleNamespace(case_ref_map={"a": {"post-a"}, "b": {"post-b"}, "noise": {"noise"}})
    fixture = {"cross_links": [{"left_case_key": "a", "right_case_key": "b"}]}
    assert AgentSuiteRunner._case_known_ids(seed, task, fixture) == {"a": {"post-a"}}
    task.tags = ("read_only", "cross_investigation")
    assert AgentSuiteRunner._case_known_ids(seed, task, fixture) == {"a": {"post-a", "post-b"}}


@pytest.mark.asyncio
async def test_policy_audit_covers_child_calls_without_exporting_input_bodies():
    class Repository:
        async def list_run_events(self, run_id):
            return [SimpleNamespace(
                id=1, event_type="tool_execution_blocked", payload={
                    "event_type": "tool_execution_blocked", "tool": "verify_claims",
                    "reason": "read_only_policy", "instructions": "private input body",
                }
            )]

        async def list_child_runs(self, run_id):
            return [SimpleNamespace(id="child")] if run_id == "root" else []

    audit = await collect_policy_audit(Repository(), "root")
    assert [item["run_id"] for item in audit] == ["root", "child"]
    assert "private input body" not in str(audit)
