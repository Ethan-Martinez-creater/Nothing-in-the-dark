"""M11: Agent Gateway — local implementation plus a remote placeholder.

The gateway is the A2A-facing entry point: ``sendTask`` submits work to an
agent, ``getTask`` reads its protocol state, ``sendMessage`` posts into its
mailbox. The local gateway maps these calls onto the durable run machinery
(``agent_runs`` + ``GraphWorker``), so A2A tasks are just another way to
address the same production path. A remote gateway is intentionally NOT
deployed in the first delivery; if ``a2a_remote_url`` is configured the
routes surface an explicit 501 instead of pretending to work.
"""

from __future__ import annotations

from typing import Protocol

from app.a2a.mailbox import TypedMailbox
from app.a2a.schemas import Message, MessageSend, Task, TaskCreate, run_status_to_task_status
from app.application.agent_service import AgentRunService
from app.application.repositories import ApplicationRepository
from app.core.errors import A2ARemoteNotDeployedError
from app.harness.agents import ExpertKind, build_definition_for


class AgentGateway(Protocol):
    """A2A gateway surface (local or remote)."""

    async def send_task(self, agent: str, request: TaskCreate) -> Task: ...
    async def get_task(self, task_id: str) -> Task: ...
    async def send_message(self, task_id: str, request: MessageSend) -> Message: ...


class LocalAgentGateway:
    """Addresses the local durable run machinery as A2A agents."""

    def __init__(
        self,
        repository: ApplicationRepository,
        agent_service: AgentRunService,
    ) -> None:
        self._repository = repository
        self._agent_service = agent_service
        self._mailbox = TypedMailbox(repository)

    async def send_task(self, agent: str, request: TaskCreate) -> Task:
        """Create and enqueue a run for the named agent.

        The coordinator is submitted through :meth:`AgentRunService.start`
        (it owns the conversation turn); expert runs are created directly
        with their own ``AgentDefinition`` and no turn, exactly like the
        ``dispatch_expert`` path. The GraphWorker picks both up durably.
        """
        if agent == "coordinator":
            run = await self._agent_service.start(
                case_id=request.case_id,
                content=request.objective,
                approve_crawl=request.approve_crawl,
            )
        else:
            # Validates the name and acts as the routing contract with the
            # worker's ``_route_definition``.
            build_definition_for(ExpertKind(agent))
            run = await self._repository.create_agent_run(
                case_id=request.case_id,
                turn_id=None,
                objective=request.objective,
                agent=agent,
                parent_run_id=request.parent_task_id,
                metadata={
                    "approve_crawl": request.approve_crawl,
                    "a2a": True,
                    **request.metadata,
                },
            )
            await self._repository.add_run_event(
                run.id,
                {
                    "event_type": "agent_queued",
                    "agent": agent,
                    "status": "pending",
                },
            )
        return await self.get_task(run.id)

    async def get_task(self, task_id: str) -> Task:
        """Read one run as an A2A Task (status, artifacts, children, history)."""
        run = await self._repository.get_agent_run(task_id)
        artifacts = await self._repository.list_run_artifacts(task_id)
        children = await self._repository.list_child_runs(task_id)
        history = await self._mailbox.list(task_id)
        return Task(
            id=run.id,
            status=run_status_to_task_status(run.status),
            agent=run.agent,
            objective=run.objective,
            parent_task_id=run.parent_run_id,
            artifacts=[
                {
                    "name": artifact.title,
                    "artifactType": artifact.kind,
                    "artifactUri": f"/api/v1/artifacts/{artifact.id}",
                    "description": None,
                    "version": artifact.version,
                }
                for artifact in artifacts
            ],
            child_tasks=[child.id for child in children],
            history=history,
            metadata={
                **(run.metadata_json or {}),
                "internal_status": run.status,
            },
            createdAt=run.created_at,
            updatedAt=run.updated_at,
        )

    async def send_message(self, task_id: str, request: MessageSend) -> Message:
        """Post a typed message from this task into another task's mailbox."""
        return await self._mailbox.send(
            sender_run_id=task_id,
            receiver_run_id=request.receiver_run_id,
            message_type=request.message_type,
            payload=request.payload,
        )


class RemoteAgentGateway:
    """Placeholder for a remote A2A service.

    The protocol surface exists (same method names, ``AgentCard.url`` can
    point at a remote base URL) but no deployment ships yet: every call
    raises so the API can answer 501 honestly.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    async def send_task(self, agent: str, request: TaskCreate) -> Task:
        raise A2ARemoteNotDeployedError()

    async def get_task(self, task_id: str) -> Task:
        raise A2ARemoteNotDeployedError()

    async def send_message(self, task_id: str, request: MessageSend) -> Message:
        raise A2ARemoteNotDeployedError()
