"""M11: A2A protocol endpoints (internal compatibility boundary).

Exposes the local agents as A2A surfaces: agent cards, task submission,
task state and the typed mailbox. When ``a2a_remote_url`` is configured the
gateway is a placeholder and every task call answers 501 (remote A2A
deployment is explicitly out of the first delivery).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.a2a.schemas import AGENT_CATALOG, AgentCard, Message, MessageSend, Task, TaskCreate
from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ApplicationError
from app.harness.agents import ExpertKind, build_coordinator_definition, build_definition_for

router = APIRouter()


def _build_agent_card(name: str) -> AgentCard:
    description, provides = AGENT_CATALOG[name]
    if name == "coordinator":
        definition = build_coordinator_definition()
        kind = "coordinator"
    else:
        definition = build_definition_for(ExpertKind(name))
        kind = "expert"
    return AgentCard(
        name=name,
        description=description,
        url=f"/api/v1/a2a/agents/{name}",
        provides=provides,
        kind=kind,
        model_route=definition.model_route,
        tools=sorted(definition.allowed_tools),
    )


@router.get("/agents", response_model=list[AgentCard])
async def list_agents() -> list[AgentCard]:
    return [_build_agent_card(name) for name in AGENT_CATALOG]


@router.get("/agents/{agent}", response_model=AgentCard)
async def get_agent(agent: str) -> AgentCard:
    if agent not in AGENT_CATALOG:
        raise ApplicationError(
            f"未知 Agent：{agent}",
            code="a2a_agent_unknown",
        )
    return _build_agent_card(agent)


@router.post("/agents/{agent}/tasks", response_model=Task, status_code=201)
async def send_task(
    agent: str,
    body: TaskCreate,
    container: ApplicationContainer = Depends(get_container),
) -> Task:
    if agent not in AGENT_CATALOG:
        raise ApplicationError(
            f"未知 Agent：{agent}",
            code="a2a_agent_unknown",
        )
    return await container.a2a_gateway.send_task(agent, body)


@router.get("/tasks/{task_id}", response_model=Task)
async def get_task(
    task_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> Task:
    return await container.a2a_gateway.get_task(task_id)


@router.get("/tasks/{task_id}/messages", response_model=list[Message])
async def list_task_messages(
    task_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[Message]:
    task = await container.a2a_gateway.get_task(task_id)
    return task.history


@router.post("/tasks/{task_id}/messages", response_model=Message, status_code=201)
async def send_task_message(
    task_id: str,
    body: MessageSend,
    container: ApplicationContainer = Depends(get_container),
) -> Message:
    return await container.a2a_gateway.send_message(task_id, body)
