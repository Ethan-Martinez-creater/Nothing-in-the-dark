"""M11: A2A protocol-compatible DTO layer (internal boundary).

The A2A spec (Agent Card / Task / Message / Artifact) is not deployed as a
remote service in the first delivery, but the protocol boundary must exist
internally so local agents can be addressed as A2A agents. This module owns
the protocol shapes and the mapping between internal ``agent_runs`` statuses
and the A2A ``TaskStatus`` machine.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    """A2A task lifecycle statuses (spec vocabulary)."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


# Internal run status -> A2A task status. ``waiting_approval`` maps to
# ``input-required``: the agent is paused and needs a human decision.
_RUN_STATUS_TO_TASK_STATUS = {
    "pending": TaskStatus.SUBMITTED,
    "running": TaskStatus.WORKING,
    "waiting_approval": TaskStatus.INPUT_REQUIRED,
    "completed": TaskStatus.COMPLETED,
    "failed": TaskStatus.FAILED,
    "cancelled": TaskStatus.CANCELED,
}


def run_status_to_task_status(run_status: str) -> TaskStatus:
    """Map an internal ``agent_runs.status`` to the A2A vocabulary."""
    try:
        return _RUN_STATUS_TO_TASK_STATUS[run_status]
    except KeyError as exc:  # pragma: no cover - guard against new statuses
        raise ValueError(f"Unknown run status: {run_status}") from exc


class MessageRole(StrEnum):
    AGENT = "agent"
    USER = "user"


class AgentCard(BaseModel):
    """A2A Agent Card for one local agent (coordinator or expert).

    ``kind`` and ``model_route`` are local extensions; ``provides`` lists
    the capability tags an external orchestrator can route on.
    """

    name: str
    description: str
    url: str
    provides: list[str] = Field(default_factory=list)
    version: str = "1.0.0"
    kind: str
    model_route: str
    tools: list[str] = Field(default_factory=list)


class TaskArtifact(BaseModel):
    """A2A TaskArtifact: a link to one persisted Artifact of a task."""

    name: str
    artifactType: str
    artifactUri: str
    description: str | None = None
    version: int = 1


class Message(BaseModel):
    """A2A Message.

    ``kind == "typed"`` carries the internal mailbox vocabulary
    (``message_type`` + ``payload``); plain ``text`` parts are supported for
    user-facing messages. ``metadata`` holds the sender/receiver run ids.
    """

    messageId: str
    role: MessageRole
    kind: str = "typed"
    message_type: str | None = None
    text: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    createdAt: datetime


class Task(BaseModel):
    """A2A Task: one internal ``agent_runs`` row plus its protocol surface."""

    id: str
    status: TaskStatus
    agent: str
    objective: str
    parent_task_id: str | None = None
    artifacts: list[TaskArtifact] = Field(default_factory=list)
    child_tasks: list[str] = Field(default_factory=list)
    history: list[Message] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    createdAt: datetime
    updatedAt: datetime


class TaskCreate(BaseModel):
    """Request body for ``sendTask`` (agent-scoped task submission)."""

    case_id: str
    objective: str
    parent_task_id: str | None = None
    approve_crawl: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageSend(BaseModel):
    """Request body for posting a typed message into a task's mailbox."""

    receiver_run_id: str
    message_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Agent catalog: description + capability tags per local agent name.
# ---------------------------------------------------------------------------

AGENT_CATALOG: dict[str, tuple[str, list[str]]] = {
    "coordinator": (
        "主协调 Agent：规划分析方案、动态委派专家、处理审批与汇报",
        ["orchestration", "dispatch", "approval"],
    ),
    "opinion": (
        "观点分析专家：情感聚类、主题识别、时间趋势与影响力排名",
        ["opinion_analysis", "sentiment", "clustering", "trends"],
    ),
    "propagation": (
        "传播复原专家：关系网络重建、媒体指纹、跨平台账号映射",
        ["propagation_reconstruction", "media_fingerprint", "account_mapping"],
    ),
    "verification": (
        "事实核查专家：主张抽取、证据检索与支持/反驳/不足判定",
        ["fact_check", "claim_extraction", "evidence_verification"],
    ),
    "evidence_critic": (
        "证据审查专家：证据链质量复核与引用一致性检查",
        ["evidence_review", "citation_check"],
    ),
    "report": (
        "报告生成专家：Markdown 结构化报告与结论逐条绑定证据",
        ["report_generation", "markdown"],
    ),
    "citation_validator": (
        "引用校验专家：核对报告结论引用的证据 ID 真实存在",
        ["citation_validation"],
    ),
}
