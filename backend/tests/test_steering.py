"""M2: mid-run steering — enqueue, graph injection and API surface.

Steering 语义：对运行中的 coordinator run 注入指令，AgentLoopGraph 在每次
``model_step`` 前经 ``steering_step`` 节点读取（crash-safe：先读后标记）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.application.repositories import ApplicationRepository
from app.bootstrap import ApplicationContainer
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.graphs.agent_loop import AgentLoopGraph
from app.harness.hooks import HookBus
from app.harness.runtime import AgentDefinition, RuntimeContext
from app.harness.tools import ToolRegistry, ToolSpec
from app.infrastructure.database import Database
from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse, ModelRoute, ToolCall
from app.main import create_app
from app.schemas.cases import CreateCaseRequest

# ---------------------------------------------------------------------------
# repository layer
# ---------------------------------------------------------------------------


async def _setup(tmp_path: Path) -> tuple[ApplicationRepository, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'steering.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="Steering 测试", platforms=["weibo"])
    )
    run = await repository.create_agent_run(
        case_id=case.id,
        turn_id=None,
        objective="初始目标",
        metadata={"approve_crawl": False},
    )
    return repository, run.id


async def test_steering_lifecycle_unconsumed_until_marked(tmp_path: Path) -> None:
    repository, run_id = await _setup(tmp_path)
    await repository.add_run_steering(run_id, "第一条指令")
    await repository.add_run_steering(run_id, "第二条指令")

    unconsumed = await repository.list_unconsumed_steerings(run_id)
    assert [record.content for record in unconsumed] == ["第一条指令", "第二条指令"]
    assert unconsumed[0].consumed_at is None

    await repository.mark_steerings_consumed(run_id)
    assert await repository.list_unconsumed_steerings(run_id) == []


# ---------------------------------------------------------------------------
# service layer (validations + events)
# ---------------------------------------------------------------------------


def _container(tmp_path: Path) -> ApplicationContainer:
    return ApplicationContainer(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'steering_service.db'}",
            demo_mode=True,
        )
    )


async def test_steer_accepts_on_pending_run_and_emits_event(tmp_path: Path) -> None:
    container = _container(tmp_path)
    await container.database.create_schema()
    repository = container.repository
    case = await repository.create_case(
        CreateCaseRequest(topic="Steering 测试", platforms=["weibo"])
    )
    run = await repository.create_agent_run(
        case_id=case.id, turn_id=None, objective="目标", metadata={}
    )
    record = await container.agent_service.steer(run.id, "请补充核查主张")
    assert record.run_id == run.id
    events = await repository.list_run_events(run.id)
    assert any(
        event.event_type == "steering_received" and event.status == "pending"
        for event in events
    )


async def test_steer_rejects_terminal_run(tmp_path: Path) -> None:
    container = _container(tmp_path)
    await container.database.create_schema()
    repository = container.repository
    case = await repository.create_case(
        CreateCaseRequest(topic="Steering 测试", platforms=["weibo"])
    )
    run = await repository.create_agent_run(
        case_id=case.id, turn_id=None, objective="目标", metadata={}
    )
    await repository.update_agent_run(run.id, status="completed")
    with pytest.raises(ApplicationError) as exc_info:
        await container.agent_service.steer(run.id, "太迟了")
    assert exc_info.value.code == "run_not_steerable"


async def test_steer_rejects_expert_run(tmp_path: Path) -> None:
    container = _container(tmp_path)
    await container.database.create_schema()
    repository = container.repository
    case = await repository.create_case(
        CreateCaseRequest(topic="Steering 测试", platforms=["weibo"])
    )
    expert = await repository.create_agent_run(
        case_id=case.id,
        turn_id=None,
        objective="专家任务",
        metadata={},
        parent_run_id="parent-1",
    )
    with pytest.raises(ApplicationError) as exc_info:
        await container.agent_service.steer(expert.id, "不该生效")
    assert exc_info.value.code == "steering_not_supported"


# ---------------------------------------------------------------------------
# graph injection (steering_step node)
# ---------------------------------------------------------------------------


class FakeGateway(LLMGateway):
    def __init__(self, script: list[LLMResponse]) -> None:
        self.script = script
        self.calls = 0
        self.history: list[list[LLMMessage]] = []

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
        self.history.append(list(messages))
        if self.calls <= len(self.script):
            return self.script[self.calls - 1]
        return _text("done")


def _text(content: str) -> LLMResponse:
    return LLMResponse(
        message=LLMMessage(role="assistant", content=content),
        estimated_cost=0.0,
        model="fake-model",
        priced=True,
    )


def _tool_call(name: str, arguments: dict[str, Any], call_id: str) -> LLMResponse:
    return LLMResponse(
        message=LLMMessage(
            role="assistant",
            tool_calls=[
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        ),
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        estimated_cost=0.0,
        model="fake-model",
        priced=True,
    )


class EchoInput(BaseModel):
    text: str


async def _echo(arguments: BaseModel) -> dict[str, Any]:
    return {"ok": True, "text": EchoInput.model_validate(arguments).text}


def _definition() -> AgentDefinition:
    return AgentDefinition(
        name="coordinator",
        instructions="按指令执行。",
        model_route=ModelRoute.FAST,
        allowed_tools=frozenset({"echo"}),
        permissions=frozenset({"read_evidence"}),
    )


def _initial_state() -> dict[str, Any]:
    return {
        "messages": [
            LLMMessage(role="system", content="系统上下文").model_dump(),
            LLMMessage(role="user", content="初始目标").model_dump(),
        ],
        "turn": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost": 0.0,
        "tool_call_count": 0,
        "status": "running",
    }


async def _run_graph(
    gateway: FakeGateway,
    loader: Any,
) -> FakeGateway:
    tools = ToolRegistry()
    tools.register(
        ToolSpec(
            name="echo",
            version="1.0.0",
            description="回显",
            input_model=EchoInput,
            handler=_echo,
            permissions=("read_evidence",),
        )
    )
    async def sink(payload: dict[str, Any]) -> None:
        return None

    graph = AgentLoopGraph(
        gateway=gateway,
        tools=tools,
        hooks=HookBus(),
        event_sink=sink,
        definition=_definition(),
        context=RuntimeContext(run_id="run-1", case_id="case-1", turn_id="turn-1"),
        checkpointer=None,
        steering_loader=loader,
    )
    await graph.ainvoke(
        _initial_state(),
        {"configurable": {"thread_id": "run-1"}},
    )
    return gateway


async def test_steering_injected_before_first_model_step() -> None:
    calls: list[str] = []

    async def loader() -> list[str]:
        calls.append("load")
        return ["steering-1"]

    gateway = await _run_graph(FakeGateway([_text("完成")]), loader)
    last_messages = gateway.history[0]
    assert last_messages[-1].role == "user"
    assert last_messages[-1].content == "steering-1"
    assert calls == ["load"]


async def test_steering_injected_mid_loop_after_tool_batch() -> None:
    calls: list[str] = []

    async def loader() -> list[str]:
        if not calls:
            calls.append("first")
            return []
        calls.append("second")
        return ["追加指令"]

    gateway = await _run_graph(
        FakeGateway([_tool_call("echo", {"text": "x"}, "call-1"), _text("完成")]),
        loader,
    )
    # 第二轮 model step 的 messages 末尾应带追加指令。
    assert len(gateway.history) == 2
    assert gateway.history[1][-1].role == "user"
    assert gateway.history[1][-1].content == "追加指令"
    assert calls == ["first", "second"]


async def test_no_loader_keeps_messages_untouched() -> None:
    gateway = await _run_graph(FakeGateway([_text("完成")]), None)
    last_messages = gateway.history[0]
    assert last_messages[-1].role == "user"
    assert last_messages[-1].content == "初始目标"


# ---------------------------------------------------------------------------
# API layer
# ---------------------------------------------------------------------------


async def _seed_case_and_run(db_path: Path) -> tuple[str, str]:
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="Steering", platforms=["weibo"])
        )
        run = await repository.create_agent_run(
            case_id=case.id, turn_id=None, objective="目标", metadata={}
        )
        return case.id, run.id
    finally:
        await database.dispose()


def test_api_steer_endpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "steering_api.db"
    _, run_id = asyncio.run(_seed_case_and_run(db_path))
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True)
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/runs/{run_id}/steering",
            json={"content": "请优先核查官方账号"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["run_id"] == run_id
        assert payload["content"] == "请优先核查官方账号"
        assert payload["consumed_at"] is None


def test_api_steer_unknown_run_returns_404(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'steering_api2.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/runs/no-such-run/steering",
            json={"content": "指令"},
        )
        assert response.status_code == 404
