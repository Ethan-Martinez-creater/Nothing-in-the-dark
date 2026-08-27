"""M11: A2A compatibility layer — DTO mapping, typed mailbox, gateway, API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.a2a import (
    AGENT_CATALOG,
    EXPERT_COMPLETED,
    LocalAgentGateway,
    RemoteAgentGateway,
    TaskStatus,
    TypedMailbox,
    run_status_to_task_status,
)
from app.a2a.schemas import MessageSend, TaskCreate
from app.application.agent_service import AgentRunService
from app.bootstrap import ApplicationContainer
from app.core.config import Settings
from app.core.errors import A2ARemoteNotDeployedError
from app.main import create_app
from app.schemas.cases import CreateCaseRequest


def _container(tmp_path: Path, name: str) -> ApplicationContainer:
    """Standalone container: ``app.state.container`` only exists inside a
    TestClient lifespan, so repository-backed tests build their own."""
    container = ApplicationContainer(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / name}", demo_mode=True)
    )
    return container

# ---------------------------------------------------------------------------
# TaskStatus mapping
# ---------------------------------------------------------------------------

def test_run_status_to_task_status_covers_the_full_machine() -> None:
    assert run_status_to_task_status("pending") is TaskStatus.SUBMITTED
    assert run_status_to_task_status("running") is TaskStatus.WORKING
    assert run_status_to_task_status("waiting_approval") is TaskStatus.INPUT_REQUIRED
    assert run_status_to_task_status("completed") is TaskStatus.COMPLETED
    assert run_status_to_task_status("failed") is TaskStatus.FAILED
    assert run_status_to_task_status("cancelled") is TaskStatus.CANCELED


def test_run_status_to_task_status_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown run status"):
        run_status_to_task_status("limbo")


def test_agent_catalog_covers_coordinator_and_all_experts() -> None:
    assert set(AGENT_CATALOG) == {
        "coordinator",
        "opinion",
        "propagation",
        "verification",
        "evidence_critic",
        "report",
        "citation_validator",
    }


# ---------------------------------------------------------------------------
# Typed mailbox (repository-backed)
# ---------------------------------------------------------------------------

async def _make_case(repository) -> str:
    case = await repository.create_case(
        CreateCaseRequest(topic="A2A 测试", platforms=["weibo"])
    )
    return case.id


async def _make_run(repository, case_id: str, *, agent: str, parent: str | None = None) -> str:
    run = await repository.create_agent_run(
        case_id=case_id,
        turn_id=None,
        objective=f"{agent} 任务",
        agent=agent,
        parent_run_id=parent,
    )
    return run.id


async def test_typed_mailbox_send_and_list(tmp_path: Path) -> None:
    container = _container(tmp_path, "a2a.db")
    await container.database.create_schema()
    repository = container.repository
    mailbox = TypedMailbox(repository)

    case_id = await _make_case(repository)
    parent = await _make_run(repository, case_id, agent="coordinator")
    child = await _make_run(repository, case_id, agent="opinion", parent=parent)

    sent = await mailbox.send_expert_completed(
        sender_run_id=child,
        receiver_run_id=parent,
        artifact_ids=["art-1"],
    )
    assert sent.message_type == EXPERT_COMPLETED
    assert sent.payload == {"artifact_ids": ["art-1"]}

    received = await mailbox.list(parent)
    assert len(received) == 1
    assert received[0].messageId == sent.messageId
    assert received[0].metadata["sender_run_id"] == child
    assert received[0].metadata["receiver_run_id"] == parent

    # Directional filters
    inbox = await mailbox.list(parent, receiver_run_id=parent)
    outbox = await mailbox.list(parent, sender_run_id=parent)
    assert len(inbox) == 1
    assert outbox == []


async def test_typed_mailbox_validates_runs(tmp_path: Path) -> None:
    container = _container(tmp_path, "a2b.db")
    await container.database.create_schema()
    mailbox = TypedMailbox(container.repository)
    with pytest.raises(Exception, match="does not exist"):
        await mailbox.send(
            sender_run_id="ghost-1",
            receiver_run_id="ghost-2",
            message_type="test",
        )


# ---------------------------------------------------------------------------
# Local gateway
# ---------------------------------------------------------------------------

async def test_local_gateway_send_task_coordinator_and_expert(tmp_path: Path) -> None:
    container = _container(tmp_path, "a2c.db")
    await container.database.create_schema()
    gateway = LocalAgentGateway(
        container.repository,
        AgentRunService(
            container.repository,
            container.worker,
        ),
    )

    case_id = await _make_case(container.repository)

    # Coordinator task: owns a conversation turn.
    coordinator_task = await gateway.send_task(
        "coordinator",
        TaskCreate(case_id=case_id, objective="完整分析", approve_crawl=True),
    )
    assert coordinator_task.status is TaskStatus.SUBMITTED
    assert coordinator_task.agent == "coordinator"
    assert coordinator_task.metadata["approve_crawl"] is True

    # Expert task: no turn, bound as a child of the coordinator.
    expert_task = await gateway.send_task(
        "opinion",
        TaskCreate(
            case_id=case_id,
            objective="观点分析",
            parent_task_id=coordinator_task.id,
            metadata={"note": "子任务"},
        ),
    )
    assert expert_task.status is TaskStatus.SUBMITTED
    assert expert_task.agent == "opinion"
    assert expert_task.parent_task_id == coordinator_task.id
    assert expert_task.metadata["note"] == "子任务"
    assert expert_task.metadata["a2a"] is True

    # Parent now lists the child; both appear in the run graph.
    parent_view = await gateway.get_task(coordinator_task.id)
    assert parent_view.child_tasks == [expert_task.id]


async def test_local_gateway_task_surfaces_artifacts_and_history(tmp_path: Path) -> None:
    container = _container(tmp_path, "a2d.db")
    await container.database.create_schema()
    repository = container.repository
    gateway = LocalAgentGateway(
        repository,
        AgentRunService(repository, container.worker),
    )

    case_id = await _make_case(repository)
    run_id = await _make_run(repository, case_id, agent="report")
    created_artifact = await repository.create_artifact(
        case_id=case_id,
        run_id=run_id,
        kind="report",
        title="A2A 报告",
        data={"title": "A2A 报告"},
    )
    other = await _make_run(repository, case_id, agent="verification")
    mailbox = TypedMailbox(repository)
    await mailbox.send(
        sender_run_id=other,
        receiver_run_id=run_id,
        message_type="deliverable",
        payload={"ids": ["x"]},
    )
    await repository.update_agent_run(run_id, status="completed")

    task = await gateway.get_task(run_id)
    assert task.status is TaskStatus.COMPLETED
    assert len(task.artifacts) == 1
    artifact = task.artifacts[0]
    assert artifact.artifactType == "report"
    assert artifact.artifactUri == f"/api/v1/artifacts/{created_artifact.id}"
    assert artifact.version == 1
    assert len(task.history) == 1
    assert task.history[0].message_type == "deliverable"
    assert task.history[0].metadata["sender_run_id"] == other


async def test_remote_gateway_is_an_explicit_placeholder() -> None:
    gateway = RemoteAgentGateway("http://remote.example")
    with pytest.raises(A2ARemoteNotDeployedError):
        await gateway.send_task("coordinator", TaskCreate(case_id="c", objective="x"))
    with pytest.raises(A2ARemoteNotDeployedError):
        await gateway.get_task("t")
    with pytest.raises(A2ARemoteNotDeployedError):
        await gateway.send_message("t", MessageSend(receiver_run_id="r", message_type="m"))


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------

def _client(tmp_path: Path, **settings_extra) -> TestClient:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'a2api.db'}",
            demo_mode=True,
            **settings_extra,
        )
    )
    return TestClient(app)


def test_a2a_agent_cards_endpoint(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        cards = client.get("/api/v1/a2a/agents")
        assert cards.status_code == 200
        names = [card["name"] for card in cards.json()]
        assert set(names) == set(AGENT_CATALOG)
        coordinator = next(card for card in cards.json() if card["name"] == "coordinator")
        assert coordinator["kind"] == "coordinator"
        assert coordinator["url"] == "/api/v1/a2a/agents/coordinator"
        opinion = next(card for card in cards.json() if card["name"] == "opinion")
        assert opinion["kind"] == "expert"
        assert "opinion_analysis" in opinion["provides"]
        assert opinion["tools"]

        unknown = client.get("/api/v1/a2a/agents/nope")
        assert unknown.status_code == 400
        assert unknown.json()["code"] == "a2a_agent_unknown"


def test_a2a_task_lifecycle_via_api(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        case = client.post(
            "/api/v1/cases",
            json={"topic": "A2A 生命周期", "platforms": ["weibo"]},
        ).json()

        submitted = client.post(
            "/api/v1/a2a/agents/opinion/tasks",
            json={"case_id": case["id"], "objective": "分析观点"},
        )
        assert submitted.status_code == 201
        task = submitted.json()
        assert task["status"] == "submitted"
        assert task["agent"] == "opinion"
        task_id = task["id"]

        fetched = client.get(f"/api/v1/a2a/tasks/{task_id}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == task_id

        messages = client.get(f"/api/v1/a2a/tasks/{task_id}/messages")
        assert messages.status_code == 200
        assert messages.json() == []


def test_a2a_mailbox_roundtrip_via_api(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        case = client.post(
            "/api/v1/cases",
            json={"topic": "A2A 信箱", "platforms": ["weibo"]},
        ).json()
        parent = client.post(
            "/api/v1/a2a/agents/coordinator/tasks",
            json={"case_id": case["id"], "objective": "协调"},
        ).json()
        child = client.post(
            "/api/v1/a2a/agents/verification/tasks",
            json={"case_id": case["id"], "objective": "核查", "parent_task_id": parent["id"]},
        ).json()

        sent = client.post(
            f"/api/v1/a2a/tasks/{child['id']}/messages",
            json={
                "receiver_run_id": parent["id"],
                "message_type": EXPERT_COMPLETED,
                "payload": {"artifact_ids": ["art-1"]},
            },
        )
        assert sent.status_code == 201
        assert sent.json()["message_type"] == EXPERT_COMPLETED

        inbox = client.get(f"/api/v1/a2a/tasks/{parent['id']}/messages")
        assert inbox.status_code == 200
        assert len(inbox.json()) == 1
        assert inbox.json()[0]["metadata"]["sender_run_id"] == child["id"]

        # Case creation no longer auto-creates a turn; only the coordinator
        # task owns a turn. The expert task created none (experts do not
        # produce conversation turns).
        turns = client.get(f"/api/v1/cases/{case['id']}/turns").json()
        assert len(turns) == 1
        assert any(turn["content"] == "协调" for turn in turns)


def test_a2a_remote_placeholder_answers_501(tmp_path: Path) -> None:
    with _client(tmp_path, a2a_remote_url="http://remote.example") as client:
        case = client.post(
            "/api/v1/cases",
            json={"topic": "A2A 远程", "platforms": ["weibo"]},
        ).json()
        response = client.post(
            "/api/v1/a2a/agents/coordinator/tasks",
            json={"case_id": case["id"], "objective": "x"},
        )
        assert response.status_code == 501
        assert response.json()["code"] == "a2a_remote_not_deployed"

        # Agent cards stay readable even with a remote gateway configured.
        cards = client.get("/api/v1/a2a/agents")
        assert cards.status_code == 200
        assert len(cards.json()) == 7
