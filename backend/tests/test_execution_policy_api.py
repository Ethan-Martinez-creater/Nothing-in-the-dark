from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.routes.cases import create_agent_message
from app.schemas.runs import AgentRunResponse, CreateMessageRequest


@pytest.mark.parametrize("invalid", [
    {"read_only": "yes"}, {"max_tool_calls": True}, {"max_tool_calls": 0},
    {"max_tool_calls": 49}, {"root_run_id": "another-run"}, {"tool_calls_used": 0},
])
def test_execution_policy_rejects_coercion_and_spoofed_runtime_state(invalid):
    with pytest.raises(ValidationError):
        CreateMessageRequest(content="read case", execution_policy=invalid)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [None, {"read_only": True, "max_tool_calls": 3}])
async def test_message_route_passes_explicit_policy_and_preserves_legacy_default(policy):
    calls = []

    class Service:
        async def start(self, **kwargs):
            calls.append(kwargs)
            now = datetime.now(UTC)
            return AgentRunResponse(
                id="run", case_id="case", turn_id=None, parent_run_id=None,
                agent="coordinator", status="pending", objective=kwargs["content"],
                model_route="fast", input_tokens=0, output_tokens=0, tool_call_count=0,
                estimated_cost=0, error_code=None, error=None,
                metadata_json={"execution_policy": kwargs.get("execution_policy")},
                created_at=now, updated_at=now,
            )

    request = CreateMessageRequest(content="read case", execution_policy=policy)
    response = await create_agent_message(
        "case", request, container=SimpleNamespace(agent_service=Service())
    )
    assert response.id == "run"
    if policy is None:
        assert "execution_policy" not in calls[0]
    else:
        assert calls[0]["execution_policy"] == {
            "read_only": True, "max_tool_calls": 3, "budget_scope": "run_tree"
        }
