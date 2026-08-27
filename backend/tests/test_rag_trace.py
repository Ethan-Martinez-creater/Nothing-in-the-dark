"""M8c: RAG hit summaries flow from tool output into runtime events and the
durable trace (ToolCallTrace.rag)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.application.repositories import ApplicationRepository
from app.harness.hooks import HookBus
from app.harness.runtime import AgentDefinition, AgentRuntime, RuntimeContext, _rag_metrics
from app.harness.tools import ToolRegistry, ToolSpec
from app.infrastructure.database import Database
from app.infrastructure.llm import (
    LLMGateway,
    LLMMessage,
    LLMResponse,
    ModelRoute,
    ToolCall,
)
from app.schemas.cases import CreateCaseRequest
from app.schemas.runs import ToolCallTrace


class SearchInput(BaseModel):
    case_id: str
    query: str


class EchoInput(BaseModel):
    text: str


class _SearchGateway(LLMGateway):
    """Issues one RAG search then one plain echo, then finishes."""

    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self.hits = hits
        self.calls = 0

    @property
    def configured(self) -> bool:
        return True

    async def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        route: ModelRoute,
        temperature: float = 0,
    ) -> LLMResponse:
        self.calls += 1
        calls = {
            1: [
                ToolCall(
                    id="call-search",
                    name="search_evidence",
                    arguments={"case_id": "case-1", "query": "谣言内容"},
                )
            ],
            2: [
                ToolCall(
                    id="call-echo",
                    name="echo",
                    arguments={"text": "done"},
                )
            ],
        }.get(self.calls, [])
        return LLMResponse(
            message=LLMMessage(
                role="assistant",
                tool_calls=[call.model_dump() for call in calls] if calls else None,
                content=None if calls else "分析完成",
            ),
            tool_calls=calls,
            model="fake-model",
        )


def _build_runtime(
    hits: list[dict[str, Any]] | None = None,
) -> tuple[AgentRuntime, ToolRegistry, list[dict[str, Any]]]:
    tools = ToolRegistry()

    async def search(arguments: BaseModel) -> dict[str, Any]:
        if hits is None:
            # worker unavailable: tool returns a non-hit payload
            return {"result": {"total": 0}}
        return {"hits": hits}

    async def echo(arguments: BaseModel) -> dict[str, Any]:
        return {"text": EchoInput.model_validate(arguments).text}

    tools.register(
        ToolSpec(
            name="search_evidence",
            version="1.0.0",
            description="Search evidence.",
            input_model=SearchInput,
            handler=search,
            permissions=("read_evidence",),
            rag_output=True,
        )
    )
    tools.register(
        ToolSpec(
            name="echo",
            version="1.0.0",
            description="Echo text.",
            input_model=EchoInput,
            handler=echo,
        )
    )
    events: list[dict[str, Any]] = []

    async def capture(event: dict[str, Any]) -> None:
        events.append(event)

    runtime = AgentRuntime(_SearchGateway(hits), tools, HookBus(), event_sink=capture)
    return runtime, tools, events


async def _run(runtime: AgentRuntime) -> None:
    await runtime.run(
        AgentDefinition(
            name="researcher",
            instructions="Search then echo.",
            model_route=ModelRoute.FAST,
            allowed_tools=frozenset({"search_evidence", "echo"}),
            permissions=frozenset({"read_evidence"}),
        ),
        user_message="检索证据",
        system_context="case=test",
        context=RuntimeContext(run_id="run-1", case_id="case-1", turn_id="turn-1"),
    )


def _end_events(events: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    return {
        event["tool_call_id"]: event.get("rag")
        for event in events
        if event["event_type"] == "tool_execution_end"
    }


async def test_rag_tool_event_carries_hit_summary() -> None:
    runtime, _, events = _build_runtime(
        [
            {
                "id": "hit-1",
                "content": "第一条证据",
                "retrieval_modes": ["vector"],
            },
            {
                "id": "hit-2",
                "content": "第二条证据",
                "retrieval_modes": ["vector", "keyword"],
            },
        ]
    )
    await _run(runtime)
    rag = _end_events(events)["call-search"]
    assert rag == {
        "available": True,
        "hit_count": 2,
        "retrieval_modes": ["keyword", "vector"],
    }


async def test_plain_tool_event_has_no_rag_field_and_worker_absent_reports_unavailable() -> None:
    # worker not configured (hits=None): the RAG tool reports unavailable
    # while a plain tool still carries no rag field at all
    runtime, _, events = _build_runtime()
    await _run(runtime)
    rags = _end_events(events)
    assert rags["call-search"] == {
        "available": False,
        "hit_count": 0,
        "retrieval_modes": [],
    }
    assert rags["call-echo"] is None


# ---------- _rag_metrics unit behaviour ----------


def test_rag_metrics_ignores_non_rag_tools() -> None:
    assert _rag_metrics(object(), {"hits": [{"retrieval_modes": ["vector"]}]}) is None


def test_rag_metrics_missing_hits_key() -> None:
    spec = ToolSpec(
        name="x",
        version="1.0.0",
        description="x",
        input_model=SearchInput,
        handler=lambda _: None,  # never called
        rag_output=True,
    )
    assert _rag_metrics(spec, {"result": 1}) == {
        "available": False,
        "hit_count": 0,
        "retrieval_modes": [],
    }


def test_rag_metrics_dedups_and_sorts_modes() -> None:
    spec = ToolSpec(
        name="x",
        version="1.0.0",
        description="x",
        input_model=SearchInput,
        handler=lambda _: None,  # never called
        rag_output=True,
    )
    assert _rag_metrics(
        spec,
        {"hits": [{"retrieval_modes": ["vector", "vector"]}, {"retrieval_modes": None}]},
    ) == {"available": True, "hit_count": 2, "retrieval_modes": ["vector"]}


# ---------- durable trace ----------


async def test_tool_call_trace_keeps_rag_after_persistence(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'trace.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="RAG 展示", platforms=["weibo"])
        )
        run = await repository.create_agent_run(
            case_id=case.id,
            turn_id=None,
            objective="RAG 展示测试",
            agent="researcher",
        )
        record = await repository.add_tool_call(
            call_id="call-1",
            run_id=run.id,
            tool_name="search_evidence",
            skill_name=None,
            status="completed",
            rag={
                "available": True,
                "hit_count": 3,
                "retrieval_modes": ["keyword", "vector"],
            },
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        trace = ToolCallTrace.model_validate(record)
        assert trace.rag == {
            "available": True,
            "hit_count": 3,
            "retrieval_modes": ["keyword", "vector"],
        }
        # default is None for non-retrieval tools
        plain = await repository.add_tool_call(
            call_id="call-2",
            run_id=run.id,
            tool_name="echo",
            skill_name=None,
            status="completed",
            started_at=datetime.now(UTC),
        )
        assert ToolCallTrace.model_validate(plain).rag is None
    finally:
        await database.dispose()
