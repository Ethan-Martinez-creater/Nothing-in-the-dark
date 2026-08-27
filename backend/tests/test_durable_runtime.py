from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy import select

from app.application.agent_service import AgentRunService
from app.application.graph_worker import GraphWorker
from app.application.ports.crawler import CrawlRequest, SocialCrawlerPort
from app.application.repositories import ApplicationRepository
from app.graphs.agent_loop import AgentLoopGraph
from app.harness.hooks import HookBus
from app.harness.runtime import AgentDefinition, RuntimeContext
from app.harness.skills import SkillRegistry
from app.harness.tool_factory import build_tool_registry
from app.harness.tools import ToolRegistry, ToolSpec
from app.infrastructure.crawler.demo import DemoCrawlerAdapter
from app.infrastructure.database import Database
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.models import ToolCallRecord
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.embeddings import EmbeddingWorkerClient
from app.infrastructure.llm import (
    LLMGateway,
    LLMMessage,
    LLMResponse,
    ModelRoute,
    ToolCall,
)
from app.schemas.cases import CreateCaseRequest


class EchoInput(BaseModel):
    text: str


class GateInput(BaseModel):
    topic: str


class SequenceGateway(LLMGateway):
    """Returns scripted responses, optionally with one tool call."""

    def __init__(self, tool_call: tuple[str, dict[str, Any]] | None = None) -> None:
        self.calls = 0
        self._tool_call = tool_call

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
        if self._tool_call is not None and self.calls == 1:
            name, arguments = self._tool_call
            return LLMResponse(
                message=LLMMessage(role="assistant"),
                tool_calls=[ToolCall(id=f"call-{self.calls}", name=name, arguments=arguments)],
                model="fake-model",
            )
        return LLMResponse(
            message=LLMMessage(role="assistant", content="任务完成"),
            model="fake-model",
        )


def _echo_tools() -> ToolRegistry:
    tools = ToolRegistry()
    executed: list[str] = []

    async def echo(arguments: BaseModel) -> dict[str, Any]:
        request = EchoInput.model_validate(arguments)
        executed.append(request.text)
        return {"text": request.text}

    tools.register(
        ToolSpec(
            name="echo",
            version="1.0.0",
            description="Echo text.",
            input_model=EchoInput,
            handler=echo,
            permissions=("read_database",),
        )
    )
    tools.executed = executed  # type: ignore[attr-defined]
    return tools


def _gate_tools() -> tuple[ToolRegistry, list[str]]:
    tools = ToolRegistry()
    executed: list[str] = []

    async def gate(arguments: BaseModel) -> dict[str, Any]:
        request = GateInput.model_validate(arguments)
        executed.append(request.topic)
        return {"ok": True}

    tools.register(
        ToolSpec(
            name="gate_tool",
            version="1.0.0",
            description="Side-effect tool that requires approval.",
            input_model=GateInput,
            handler=gate,
            permissions=("crawl_platform",),
            side_effect="external_read",
            requires_approval=True,
        )
    )
    return tools, executed


def _initial_state() -> dict[str, Any]:
    return {
        "messages": [
            LLMMessage(role="system", content="sys").model_dump(),
            LLMMessage(role="user", content="开始").model_dump(),
        ],
        "turn": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost": 0.0,
        "tool_call_count": 0,
        "status": "running",
    }


def _definition(allowed: frozenset[str], *, approval: bool = False) -> AgentDefinition:
    return AgentDefinition(
        name="researcher",
        instructions="Use tools.",
        model_route=ModelRoute.FAST,
        allowed_tools=allowed,
        permissions=frozenset({"read_database", "crawl_platform"}),
    )


async def test_graph_approval_interrupt_then_resume() -> None:
    tools, executed = _gate_tools()
    gateway = SequenceGateway(tool_call=("gate_tool", {"topic": "舆情"}))
    graph = AgentLoopGraph(
        gateway=gateway,
        tools=tools,
        hooks=HookBus(),
        event_sink=lambda _: _noop(),
        definition=_definition(frozenset({"gate_tool"})),
        context=RuntimeContext(run_id="run-a", case_id="case-a", turn_id="turn-a"),
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "run-a"}}

    state = await graph.ainvoke(_initial_state(), config)
    assert state.get("__interrupt__"), "approval must pause the run"
    assert executed == [], "tool must not run before approval"

    state = await graph.ainvoke(
        Command(resume={"approved": True, "approval_id": "a1"}), config
    )
    assert state["status"] == "completed"
    assert executed == ["舆情"], "tool runs exactly once after approval"


async def test_graph_approval_rejected_tool_not_executed() -> None:
    tools, executed = _gate_tools()
    gateway = SequenceGateway(tool_call=("gate_tool", {"topic": "舆情"}))
    graph = AgentLoopGraph(
        gateway=gateway,
        tools=tools,
        hooks=HookBus(),
        event_sink=lambda _: _noop(),
        definition=_definition(frozenset({"gate_tool"})),
        context=RuntimeContext(run_id="run-b", case_id="case-a", turn_id="turn-a"),
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "run-b"}}

    state = await graph.ainvoke(_initial_state(), config)
    assert state.get("__interrupt__")

    state = await graph.ainvoke(
        Command(resume={"approved": False, "approval_id": "a2"}), config
    )
    assert state["status"] == "completed"
    assert executed == [], "rejected tool must never run"
    assert any(
        m.get("role") == "tool" and "tool_rejected_by_user" in str(m.get("content", ""))
        for m in state["messages"]
    ), "model must see the rejection result"


async def test_graph_resumes_from_checkpoint_after_crash() -> None:
    """A new graph instance with the same saver continues the paused run."""
    saver = MemorySaver()
    tools, executed = _gate_tools()
    gateway = SequenceGateway(tool_call=("gate_tool", {"topic": "舆情"}))

    def build() -> AgentLoopGraph:
        return AgentLoopGraph(
            gateway=gateway,
            tools=tools,
            hooks=HookBus(),
            event_sink=lambda _: _noop(),
            definition=_definition(frozenset({"gate_tool"})),
            context=RuntimeContext(run_id="run-c", case_id="case-a", turn_id="turn-a"),
            checkpointer=saver,
        )

    config = {"configurable": {"thread_id": "run-c"}}
    crashed = build()
    state = await crashed.ainvoke(_initial_state(), config)
    assert state.get("__interrupt__")
    # Simulate a process crash: the instance is discarded without a resume.

    recovered = build()
    snapshot = await recovered.aget_state(config)
    assert snapshot.interrupts, "interrupt must survive in the checkpoint"

    state = await recovered.ainvoke(
        Command(resume={"approved": True, "approval_id": "a3"}), config
    )
    assert state["status"] == "completed"
    assert executed == ["舆情"], "tool executes exactly once across restart"


async def test_repository_tool_call_idempotency(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'idem.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="幂等测试", platforms=["weibo"])
    )
    turn = await repository.add_turn(case.id, role="user", content="测试")
    run = await repository.create_agent_run(
        case_id=case.id, turn_id=turn.id, objective="测试"
    )
    try:
        first = await repository.add_tool_call(
            call_id="call-1",
            run_id=run.id,
            tool_name="echo",
            skill_name=None,
            status="completed",
            arguments={"text": "x"},
            result={"data": {"text": "x"}},
            idempotency_key=f"{run.id}:call-1",
        )
        second = await repository.add_tool_call(
            call_id="call-1",
            run_id=run.id,
            tool_name="echo",
            skill_name=None,
            status="completed",
            arguments={"text": "x"},
            result={"data": {"text": "x"}},
            idempotency_key=f"{run.id}:call-1",
        )
        assert second.id == first.id, "duplicate idempotency key must be deduplicated"
        async with database.session_factory() as session:
            count = await session.scalar(
                select(ToolCallRecord.id)
                .where(ToolCallRecord.run_id == run.id)
            )
            assert count is not None
    finally:
        await database.dispose()


class CountingCrawler(SocialCrawlerPort):
    def __init__(self) -> None:
        self.calls = 0
        self._inner = DemoCrawlerAdapter()

    async def collect(self, request: CrawlRequest) -> list[dict[str, Any]]:
        self.calls += 1
        return await self._inner.collect(request)


class CrawlerSandboxStub:
    """Test boundary that preserves sandbox dispatch while injecting a fake crawler."""

    def __init__(self, crawler: SocialCrawlerPort) -> None:
        self._crawler = crawler

    async def execute(self, *, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        request = CrawlRequest(
            topic=str(payload.get("topic") or ""),
            platforms=list(payload.get("platforms") or []),
            time_range=dict(payload.get("time_range") or {}),
            limit_per_platform=int(payload.get("limit_per_platform") or 150),
            per_day_limit=int(payload.get("per_day_limit") or 150),
            comment_limit=int(payload.get("comment_limit") or 10),
            keywords=dict(payload.get("keywords") or {}),
        )
        posts = await self._crawler.collect(request)
        return {"ok": True, "posts": posts, "platforms": request.platforms}


async def _build_worker_env(tmp_path: Path) -> tuple[
    Database,
    ApplicationRepository,
    GraphWorker,
    AgentRunService,
    CountingCrawler,
]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    knowledge = KnowledgeRepository(database)
    social = SocialRepository(database)
    crawler = CountingCrawler()
    skills = SkillRegistry()
    tools = build_tool_registry(
        crawler,
        skills,
        knowledge,
        EmbeddingWorkerClient(
            "", dimensions=1024, timeout_seconds=120
        ),
        social,
    )
    tools.set_sandbox_executor(CrawlerSandboxStub(crawler))
    crawl_args: dict[str, Any] = {
        "topic": "舆情",
        "platforms": ["weibo"],
        "time_range": {"start": None, "end": None},
    }
    worker = GraphWorker(
        repository,
        SequenceGateway(tool_call=("collect_social_posts", crawl_args)),
        tools,
        skills,
        worker_id="test-worker",
        poll_interval_seconds=0.01,
        lease_seconds=300,
        max_turns=8,
        max_tool_calls=16,
        max_cost=5.0,
        checkpointer=MemorySaver(),
    )
    service = AgentRunService(repository, worker)
    return database, repository, worker, service, crawler


async def test_worker_end_to_end_approval_flow(tmp_path: Path) -> None:
    database, repository, worker, service, crawler = await _build_worker_env(tmp_path)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="审批流程", platforms=["weibo"])
        )
        run = await service.start(
            case_id=case.id, content="采集并分析", approve_crawl=False
        )
        assert run.status == "pending"

        await worker.tick(wait=True)
        run = await repository.get_agent_run(run.id)
        assert run.status == "waiting_approval"
        assert crawler.calls == 0, "crawler must not run before approval"

        approvals = await repository.list_pending_approvals(run.id)
        assert len(approvals) == 1
        assert approvals[0].action == "collect_social_posts"
        tool_calls = await repository.list_run_tool_calls(run.id)
        assert tool_calls and tool_calls[0].status == "waiting_approval"
        assert tool_calls[0].approval_id == approvals[0].id

        run = await service.approve(
            run.id, approval_id=approvals[0].id, decision="approve"
        )
        assert run.status == "pending"
        await worker.tick(wait=True)

        run = await repository.get_agent_run(run.id)
        assert run.status == "completed", run.error
        assert crawler.calls == 1, "crawler runs exactly once after approval"
        trace = await repository.get_run_trace(run.id)
        assert len(trace["tool_calls"]) == 1
        assert trace["tool_calls"][0].status == "completed"
        assert len(trace["model_calls"]) == 2
        assert trace["approvals"][0].status == "approved"
        turns = await repository.list_turns(case.id)
        assert any(turn.role == "assistant" for turn in turns)
    finally:
        await database.dispose()


async def test_worker_end_to_end_approval_reject_flow(tmp_path: Path) -> None:
    """拒绝审批后恢复执行：不重复插入 tool_call、run 正常完成。"""
    database, repository, worker, service, crawler = await _build_worker_env(
        tmp_path
    )
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="拒绝审批", platforms=["weibo"])
        )
        run = await service.start(
            case_id=case.id, content="采集并分析", approve_crawl=False
        )
        await worker.tick(wait=True)
        run = await repository.get_agent_run(run.id)
        assert run.status == "waiting_approval"

        approvals = await repository.list_pending_approvals(run.id)
        assert len(approvals) == 1
        blocked = await repository.list_run_tool_calls(run.id)
        assert blocked and blocked[0].status == "waiting_approval"
        blocked_call_id = blocked[0].id

        run = await service.approve(
            run.id, approval_id=approvals[0].id, decision="reject"
        )
        await worker.tick(wait=True)

        run = await repository.get_agent_run(run.id)
        assert run.status == "completed", run.error
        assert crawler.calls == 0, "rejected crawler must never run"
        trace = await repository.get_run_trace(run.id)
        call_ids = [call.id for call in trace["tool_calls"]]
        assert len(call_ids) == len(set(call_ids)), "no duplicate tool calls"
        # 被拒的调用被更新为终态，而不是重复插一行。
        blocked_rows = [
            call for call in trace["tool_calls"] if call.id == blocked_call_id
        ]
        assert len(blocked_rows) == 1
        assert blocked_rows[0].status in {"rejected", "cancelled", "completed"}
    finally:
        await database.dispose()


async def test_worker_preapproved_crawl_executes_directly(tmp_path: Path) -> None:
    database, repository, worker, service, crawler = await _build_worker_env(tmp_path)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="预批准采集", platforms=["weibo"])
        )
        run = await service.start(
            case_id=case.id, content="采集并分析", approve_crawl=True
        )
        await worker.tick(wait=True)
        run = await repository.get_agent_run(run.id)
        assert run.status == "completed", run.error
        assert crawler.calls == 1, "pre-approved crawl executes directly"
        assert await repository.list_pending_approvals(run.id) == []
    finally:
        await database.dispose()


async def _noop() -> None:
    return None
