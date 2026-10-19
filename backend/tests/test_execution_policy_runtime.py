"""Execution policy regressions against real runtime, repositories and worker."""

import asyncio
import json

import pytest
from pydantic import BaseModel
from test_expert_agents import (
    ScriptedGateway,
    _build_worker,
    _response,
    _tool_response,
    _wait_for_run,
)

from app.application.agent_service import AgentRunService
from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.evaluation.agent_eval_runtime import configure_disposable_sqlite
from app.harness.hooks import HookBus
from app.harness.runtime import AgentDefinition, AgentRuntime, RuntimeContext
from app.harness.tools import ToolRegistry, ToolSpec
from app.infrastructure.database import Database
from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse, ModelRoute, ToolCall
from app.schemas.cases import CreateCaseRequest


class EmptyInput(BaseModel):
    pass


class BatchGateway(LLMGateway):
    configured = True

    def __init__(self, calls):
        self.calls = calls
        self.requests = 0
        self.offered = set()

    async def complete(self, *, messages, tools, route, temperature=0):
        self.requests += 1
        self.offered = {item["function"]["name"] for item in tools}
        assert self.requests == 1, "Policy must stop before another model request"
        return LLMResponse(
            message=LLMMessage(role="assistant"), tool_calls=self.calls, model="fake"
        )


def policy(limit=3, read_only=True):
    return {"read_only": read_only, "max_tool_calls": limit, "budget_scope": "run_tree"}


@pytest.mark.asyncio
async def test_failed_batch_consumes_only_three_slots_and_stops():
    invocations = []

    async def failing(_):
        invocations.append(1)
        raise ApplicationError("injected", code="eval_injected_persistent_read")

    tools = ToolRegistry()
    tools.register(ToolSpec("read", "1", "read", EmptyInput, failing))
    gateway = BatchGateway([ToolCall(id=str(i), name="read", arguments={}) for i in range(5)])
    events = []

    async def sink(event):
        events.append(event)

    runtime = AgentRuntime(gateway, tools, HookBus(), event_sink=sink)
    context = RuntimeContext("run", "case", "turn", metadata={"execution_policy": policy()})
    result = await runtime.run(
        AgentDefinition("test", "test", ModelRoute.FAST, frozenset({"read"})),
        user_message="read",
        system_context="",
        context=context,
    )
    assert len(invocations) == result.tool_calls == 3
    assert gateway.requests == 1
    assert len([e for e in events if e["event_type"] == "tool_execution_blocked"]) == 2
    assert events[-1]["reason"] == "tool_budget_exhausted"
    assert "不提供未经核验的精确统计" in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "names,ids,reason",
    [
        (["write_case_memory"], ["a"], "read_only_policy"),
        (["unknown"], ["a"], "invalid_tool_request"),
        (["read", "read"], ["same", "same"], "invalid_tool_request"),
    ],
)
async def test_forbidden_or_invalid_calls_do_not_escape_policy(names, ids, reason):
    invoked = []

    async def handler(_):
        invoked.append(1)
        return {"ok": True}

    tools = ToolRegistry()
    for name in ("read", "write_case_memory"):
        tools.register(ToolSpec(name, "1", name, EmptyInput, handler))
    gateway = BatchGateway([
        ToolCall(id=i, name=n, arguments={}) for n, i in zip(names, ids, strict=True)
    ])
    context = RuntimeContext("run", "case", "turn", metadata={"execution_policy": policy()})
    result = await AgentRuntime(gateway, tools, HookBus()).run(
        AgentDefinition("test", "test", ModelRoute.FAST, frozenset(tools.names())),
        user_message="read",
        system_context="",
        context=context,
    )
    assert "write_case_memory" not in gateway.offered
    assert context.metadata["_execution_stop_reason"] == reason
    assert len(invoked) == result.tool_calls <= 1
    if reason == "read_only_policy":
        assert not invoked


@pytest.mark.asyncio
async def test_two_repository_instances_share_atomic_sqlite_reservations(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'budget.db'}")
    configure_disposable_sqlite(database)
    await database.create_schema()
    repo = ApplicationRepository(database)
    try:
        case = await repo.create_case(CreateCaseRequest(topic="synthetic", platforms=["weibo"]))
        run = await repo.create_agent_run(
            case_id=case.id,
            turn_id=None,
            objective="budget",
            metadata={"execution_policy": policy()},
        )
        other = ApplicationRepository(database)
        receipts = await asyncio.gather(
            *[(repo if i % 2 else other).reserve_run_tree_tool_calls(run.id, 1) for i in range(10)]
        )
        assert sum(r["reserved"] for r in receipts) == 3
        assert (await other.get_run_tree_tool_budget(run.id))["used"] == 3
    finally:
        await database.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("readonly,try_tool", [(True, True), (True, False), (False, True)])
async def test_delegated_verification_write_boundary_and_writable_compatibility(
    tmp_path,
    readonly,
    try_tool,
):
    cards = json.dumps(
        {
            "cards": [
                {
                    "claim": "synthetic assertion",
                    "verdict": "insufficient",
                    "confidence": 0.5,
                    "reason": "synthetic",
                    "supporting_evidence": [],
                    "contradicting_evidence": [],
                }
            ]
        }
    )
    expert = [_response(cards)]
    if try_tool:
        expert.insert(
            0,
            _tool_response(
                "verify_claims",
                {
                    "topic": "synthetic",
                    "posts": [
                        {
                            "id": "synthetic-post",
                            "content": "测试事件导致十人受伤。",
                            "platform": "weibo",
                            "author": "test",
                        }
                    ],
                },
            ),
        )
    gateway = ScriptedGateway(
        {
            "coordinator": [
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "verification",
                        "instructions": "核查",
                        "input_data": {},
                        "execution_policy": {"read_only": False, "root_run_id": "forged"},
                    },
                ),
                _response("finished"),
            ],
            "verification": expert,
        }
    )
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'delegated.db'}")
    configure_disposable_sqlite(database)
    worker, repo, database = await _build_worker(tmp_path, gateway, database=database)
    try:
        case = await repo.create_case(CreateCaseRequest(topic="synthetic", platforms=["weibo"]))
        run = await AgentRunService(repo, worker).start(
            case_id=case.id,
            content="核查",
            approve_crawl=False,
            execution_policy=policy(12, readonly),
        )
        await worker.start()
        finished = await _wait_for_run(repo, run.id)
        assert finished.status == "completed", finished.error_message
        children = await repo.list_child_runs(run.id)
        assert len(children) == 1
        child = children[0]
        child_policy = child.metadata_json["execution_policy"]
        assert child_policy["read_only"] == readonly
        assert child_policy["root_run_id"] == run.id
        claims = await repo.list_claims_by_case(case.id)
        evidence = await repo.list_evidence_by_case(case.id)
        artifacts = await repo.list_artifacts(case.id)
        if readonly:
            assert not claims and not evidence and not artifacts
            events = await repo.list_run_events(child.id)
            if try_tool:
                assert any(e.event_type == "tool_execution_blocked" for e in events)
            else:
                messages = await repo.list_agent_messages(run.id)
                assert any(m.payload.get("artifact_id") is None for m in messages)
        else:
            assert len(claims) >= 2 and evidence and artifacts
    finally:
        await worker.stop()
        await database.dispose()


@pytest.mark.asyncio
async def test_dispatch_and_child_reads_share_three_call_limit(tmp_path):
    gateway = ScriptedGateway(
        {
            "coordinator": [
                _tool_response(
                    "dispatch_expert",
                    {"agent": "verification", "instructions": "read", "input_data": {}},
                ),
            ],
            "verification": [
                LLMResponse(
                    message=LLMMessage(role="assistant"),
                    tool_calls=[
                        ToolCall(id=str(i), name="query_claims", arguments={}) for i in range(4)
                    ],
                    model="fake",
                )
            ],
        }
    )
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'tree.db'}")
    configure_disposable_sqlite(database)
    worker, repo, database = await _build_worker(tmp_path, gateway, database=database)
    try:
        case = await repo.create_case(CreateCaseRequest(topic="synthetic", platforms=["weibo"]))
        run = await AgentRunService(repo, worker).start(
            case_id=case.id, content="read", approve_crawl=False, execution_policy=policy()
        )
        await worker.start()
        finished = await _wait_for_run(repo, run.id)
        assert finished.status == "completed"
        assert (await repo.get_run_tree_tool_budget(run.id))["used"] == 3
        children = await repo.list_child_runs(run.id)
        calls = await repo.list_run_tool_calls(run.id)
        calls += await repo.list_run_tool_calls(children[0].id)
        assert len(calls) == 3
        assert gateway._counters["coordinator"] == gateway._counters["verification"] == 1
        assert not await repo.list_artifacts(case.id)
    finally:
        await worker.stop()
        await database.dispose()
