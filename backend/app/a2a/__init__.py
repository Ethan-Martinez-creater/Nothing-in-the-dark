"""M11: A2A protocol compatibility layer.

Local-only in the first delivery: protocol DTOs (``schemas``), the typed
mailbox (``mailbox``) and the gateway split (``gateway`` — local durable
machinery vs. a remote placeholder). See ``README`` § A2A.
"""

from app.a2a.gateway import LocalAgentGateway, RemoteAgentGateway
from app.a2a.mailbox import EXPERT_COMPLETED, TypedMailbox
from app.a2a.schemas import (
    AGENT_CATALOG,
    AgentCard,
    Message,
    MessageRole,
    MessageSend,
    Task,
    TaskArtifact,
    TaskCreate,
    TaskStatus,
    run_status_to_task_status,
)

__all__ = [
    "AGENT_CATALOG",
    "AgentCard",
    "EXPERT_COMPLETED",
    "LocalAgentGateway",
    "Message",
    "MessageRole",
    "MessageSend",
    "RemoteAgentGateway",
    "Task",
    "TaskArtifact",
    "TaskCreate",
    "TaskStatus",
    "TypedMailbox",
    "run_status_to_task_status",
]
