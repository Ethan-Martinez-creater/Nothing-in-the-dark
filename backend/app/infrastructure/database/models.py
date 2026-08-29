from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class _Utf8JSON(TypeDecorator):
    """JSON column storing unescaped unicode TEXT on every dialect.

    The SQLite dialect replaces plain JSON columns with its own processor
    (which escapes unicode), breaking ``ilike`` keyword search on Chinese
    content. A native PostgreSQL ``json`` column is no better: ``json_out``
    escapes non-ASCII characters, so ``data::text`` (and the tsvector
    generated column built from it) never contains the literal Chinese text
    either. Storing the serialized JSON as TEXT (migration 20260806_0011)
    keeps the real characters on both dialects; ``json.loads`` restores the
    value on read.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, default=str)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        return value  # the JSON impl already parsed for PostgreSQL


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class CaseRecord(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    topic: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    time_range: Mapped[dict[str, str | None]] = mapped_column(JSON, default=dict)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )

    turns: Mapped[list[ConversationTurnRecord]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
    )
    tasks: Mapped[list[AnalysisTaskRecord]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
    )
    artifacts: Mapped[list[ArtifactRecord]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
    )


class ConversationTurnRecord(Base):
    __tablename__ = "conversation_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    role: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    case: Mapped[CaseRecord] = relationship(back_populates="turns")


class AnalysisTaskRecord(Base):
    __tablename__ = "analysis_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    current_stage: Mapped[str] = mapped_column(String(100), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0)
    options: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )

    case: Mapped[CaseRecord] = relationship(back_populates="tasks")
    events: Mapped[list[TaskEventRecord]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )


class TaskEventRecord(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("analysis_tasks.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    progress: Mapped[float] = mapped_column(Float, default=0)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    task: Mapped[AnalysisTaskRecord] = relationship(back_populates="events")


class ArtifactRecord(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_tasks.id"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict[str, object]] = mapped_column(_Utf8JSON)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    case: Mapped[CaseRecord] = relationship(back_populates="artifacts")


class AgentRunRecord(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversation_turns.id"), nullable=True, index=True
    )
    parent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    agent: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    objective: Mapped[str] = mapped_column(Text)
    model_route: Mapped[str] = mapped_column(String(32), default="fast")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    # M17: 目标/计划图关联（可空，普通对话运行不绑定）。
    goal_id: Mapped[str | None] = mapped_column(
        ForeignKey("goals.id"), nullable=True, index=True
    )
    plan_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("plan_versions.id"), nullable=True
    )
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("plan_steps.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AgentMessageRecord(Base):
    """Typed mailbox message between parent and child agent runs."""

    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sender_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id"), index=True
    )
    receiver_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id"), index=True
    )
    message_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RunSteeringRecord(Base):
    """Steering instructions injected into a running coordinator run.

    ``consumed_at`` is set when the worker folds the instruction into the
    agent loop, so a steering never applies twice (crash-safe: the worker
    marks consumption only after loading them).
    """

    __tablename__ = "run_steerings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ModelCallRecord(Base):
    __tablename__ = "model_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    model: Mapped[str] = mapped_column(String(200))
    route: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    pricing_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class RunEventRecord(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    agent: Mapped[str] = mapped_column(String(100), default="")
    skill: Mapped[str | None] = mapped_column(String(160), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    # M19: OTel 风格 trace 关联（SSE 事件打开对应运行）。
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ToolCallRecord(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(160), index=True)
    skill_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    arguments: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_history: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, default=list
    )
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(100), nullable=True, unique=True
    )
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # M8c: structured RAG hit summary ({available, hit_count, retrieval_modes})
    # for retrieval tools; NULL for every other tool
    rag: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ApprovalRecord(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    action: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    request_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    decision_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    # M21: 广义人工介入——类型、策略版本、风险、作用域与编辑批准。
    approval_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_action: Mapped[str | None] = mapped_column(String(200), nullable=True)
    redacted_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_decisions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    edited_action: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resume_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ExecutionAuthorizationRecord(Base):
    """一次性执行授权（21）：绑定 tool+参数哈希+run+期限，消费后失效。"""

    __tablename__ = "execution_authorizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(160), default="")
    argument_hash: Mapped[str] = mapped_column(String(64), default="")
    # M21/M22 一次性消费：action_family 标识操作族（tool:xxx / kill_switch /
    # dead_letter_retry），resource_id 绑定具体资源（run_id / 开关目标 / 死信 id）。
    action_family: Mapped[str] = mapped_column(String(64), default="")
    resource_id: Mapped[str] = mapped_column(String(160), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        # 一个审批至多一条执行授权：杜绝同一审批重复用于多个操作。
        UniqueConstraint("approval_id", name="uq_execution_authorization_approval"),
    )


class MemoryRecord(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.id"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(500))
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    confidence: Mapped[float] = mapped_column(Float, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("memories.id"), nullable=True
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    # M23: 记忆安全与治理——类型/信任/置信/审核/有效期/敏感/版本/状态。
    memory_type: Mapped[str] = mapped_column(
        String(40), default="case_fact", index=True
    )
    trust_level: Mapped[str] = mapped_column(
        String(32), default="external_content"
    )
    review_state: Mapped[str] = mapped_column(
        String(32), default="unreviewed", index=True
    )
    confidence_level: Mapped[str] = mapped_column(
        String(16), default="medium"
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    sensitivity: Mapped[str] = mapped_column(String(16), default="low")
    index_status: Mapped[str] = mapped_column(String(16), default="pending")
    embedding_version: Mapped[str] = mapped_column(String(32), default="")
    write_policy_version: Mapped[str] = mapped_column(String(32), default="")
    # active / pending_review / superseded / expired / disabled / deleted
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)


class KnowledgeDocumentRecord(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    media_type: Mapped[str] = mapped_column(String(100))
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeChunkRecord(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)

    __table_args__ = (UniqueConstraint("document_id", "ordinal"),)


class SourcePostRecord(Base):
    __tablename__ = "source_posts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    native_id: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(32), default="post")
    title: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str] = mapped_column(String(200), default="")
    author_name: Mapped[str] = mapped_column(String(300), default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    engagement: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (UniqueConstraint("case_id", "platform", "native_id"),)


class SourceCommentRecord(Base):
    __tablename__ = "source_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    post_id: Mapped[str] = mapped_column(ForeignKey("source_posts.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    native_id: Mapped[str] = mapped_column(String(200))
    parent_native_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str] = mapped_column(String(200), default="")
    author_name: Mapped[str] = mapped_column(String(300), default="")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metrics: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)

    __table_args__ = (UniqueConstraint("post_id", "platform", "native_id"),)


class RawSocialRecord(Base):
    __tablename__ = "raw_social_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    record_type: Mapped[str] = mapped_column(String(32), index=True)
    native_id: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "platform",
            "record_type",
            "native_id",
            "checksum",
        ),
    )


class PlatformCapabilityRecord(Base):
    __tablename__ = "platform_capabilities"

    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(
        String(32), default="validation_required", index=True
    )
    checks: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ClaimRecord(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    created_by_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvidenceRecord(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    claim_id: Mapped[str | None] = mapped_column(
        ForeignKey("claims.id"), nullable=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(500))
    stance: Mapped[str] = mapped_column(String(32), default="context")
    excerpt: Mapped[str] = mapped_column(Text)
    relevance: Mapped[float] = mapped_column(Float, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PropagationEdgeRecord(Base):
    __tablename__ = "propagation_edges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    source_post_id: Mapped[str] = mapped_column(ForeignKey("source_posts.id"))
    target_post_id: Mapped[str] = mapped_column(ForeignKey("source_posts.id"))
    relation: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    feature_scores: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    algorithm_version: Mapped[str] = mapped_column(String(64))
    human_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("case_id", "source_post_id", "target_post_id"),
    )


class AccountRecord(Base):
    """Platform accounts observed during collection.

    Accounts are global (a person appears across cases); ``case_id`` records
    the case where the account was first observed. ``normalized_name`` is the
    cross-platform matching key; ``is_authoritative`` marks official accounts
    for the Verification whitelist.
    """

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.id"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(32), index=True)
    native_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300), default="")
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    follower_count: Mapped[int] = mapped_column(Integer, default=0)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_authoritative: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True
    )
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (UniqueConstraint("platform", "native_id"),)


class MediaAssetRecord(Base):
    """Media attached to posts: images, videos and audio.

    ``normalized_url`` is the deduplication/cross-platform matching key (query
    params stripped). ``phash``/``ocr_text``/``keyframe_urls`` are filled by
    the media analysis pipeline when a local file is available; with URL-only
    evidence they stay NULL and similarity falls back to URL matching.
    """

    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    post_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_posts.id"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(32))
    media_type: Mapped[str] = mapped_column(String(32), default="image")
    url: Mapped[str] = mapped_column(Text)
    normalized_url: Mapped[str] = mapped_column(Text, index=True)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyframe_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    # ---- 04 多模态流水线扩展 ----
    source_kind: Mapped[str] = mapped_column(String(32), default="url")
    storage_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str] = mapped_column(String(100), default="")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_status: Mapped[str] = mapped_column(
        String(32), default="not_downloaded", index=True
    )
    analysis_status: Mapped[str] = mapped_column(
        String(32), default="pending", index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actual_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hash_kind: Mapped[str] = mapped_column(
        String(32), default="url_fingerprint_legacy"
    )
    c2pa_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pipeline_version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("case_id", "normalized_url", "post_id"),
    )


class MediaDerivativeRecord(Base):
    """派生文件：OCR/ASR 文本、抽帧、缩略图等，带时间码/坐标与来源关系。"""

    __tablename__ = "media_derivatives"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), index=True)
    storage_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    producer: Mapped[str] = mapped_column(String(100), default="")
    version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("asset_id", "kind", "producer", "version"),
    )


class MediaTranscriptRecord(Base):
    """音视频/图片转录：字幕、ASR、OCR 分段文本与置信度。"""

    __tablename__ = "media_transcripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="asr")
    language: Mapped[str] = mapped_column(String(16), default="")
    segments: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    full_text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0)
    provider: Mapped[str] = mapped_column(String(100), default="")
    version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (UniqueConstraint("asset_id", "kind", "provider", "version"),)


class MediaPipelineJobRecord(Base):
    """媒体流水线阶段任务：租约、重试、阶段级状态与资源统计。"""

    __tablename__ = "media_pipeline_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32), index=True)
    pipeline_version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resource_stats: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (UniqueConstraint("asset_id", "stage", "pipeline_version"),)


class EntityRecord(Base):
    """Named entities extracted from collected posts."""

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text, index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    mentions_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("case_id", "entity_type", "normalized_name"),
    )


class PropagationNodeRecord(Base):
    """Node roles computed by the propagation algorithm: source, bridge,
    burst and hub candidates."""

    __tablename__ = "propagation_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    post_id: Mapped[str] = mapped_column(ForeignKey("source_posts.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[float] = mapped_column(Float, default=0)
    attributes: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    algorithm_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (UniqueConstraint("case_id", "post_id", "role"),)


class EvaluationRecord(Base):
    """Metric results for the Evaluation milestone (per run or per case)."""

    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.id"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    metric: Mapped[str] = mapped_column(String(100), index=True)
    score: Mapped[float] = mapped_column(Float)
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CostSummaryRecord(Base):
    """Aggregated cost records (model + tool) for a run or a case."""

    __tablename__ = "cost_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    summary_type: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True, unique=True
    )
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.id"), nullable=True, index=True
    )
    model_cost: Mapped[float] = mapped_column(Float, default=0)
    tool_cost: Mapped[float] = mapped_column(Float, default=0)
    total_cost: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    period: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EmbeddingVersionRecord(Base):
    """The embedding model version in effect for vector columns.

    ``rebuilt_at`` records when a backfill last refreshed the rows for
    that version; a model swap on the worker therefore shows up as a new
    row here, and the backfill can warn before mixing vectors from two
    different models.
    """

    __tablename__ = "embedding_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_name: Mapped[str] = mapped_column(String(300), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    dimensions: Mapped[int] = mapped_column(Integer, default=1024)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    rebuilt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class DebateRecord(Base):
    """一次多角色辩论：以各平台采集数据为背景知识，四轮（陈述→反驳→
    投票→主持人总结）逼近事实结论，用户可随时插话。"""

    __tablename__ = "debates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="多平台观点辩论")
    status: Mapped[str] = mapped_column(String(32), default="in_progress", index=True)
    # 当前轮次：1 陈述 / 2 反驳 / 3 投票 / 4 主持人总结
    round: Mapped[int] = mapped_column(Integer, default=1)
    # 参与角色（平台名列表），生成辩论消息时按此展开
    platform_roles: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DebateMessageRecord(Base):
    """辩论中的一条发言：平台角色 / 用户插话 / 主持人总结。"""

    __tablename__ = "debate_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    debate_id: Mapped[str] = mapped_column(ForeignKey("debates.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), index=True)  # platform_role|user|moderator
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    round: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DebateVoteRecord(Base):
    """第三轮投票：每个平台角色投出"最接近事实的平台立场"及理由。"""

    __tablename__ = "debate_votes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    debate_id: Mapped[str] = mapped_column(ForeignKey("debates.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32))
    choice: Mapped[str] = mapped_column(String(32))  # 投给哪个平台的立场
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProjectRecord(Base):
    """项目：会话（case）的上级容器，侧边栏按项目分组展示对话记录。"""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class MonitorDefinitionRecord(Base):
    """连续监测定义：为案件配置主题/关键词/平台/观察名单与调度。

    监测由独立 MonitorScheduler 按 schedule_type 触发，采集结果复用现有
    统一去重与持久化路径；分析运行通过 AgentRunService 创建，不直接调用
    Agent 内部实现。
    """

    __tablename__ = "monitor_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    schedule_type: Mapped[str] = mapped_column(String(32), default="interval")
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    query_spec: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    account_watchlist: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, default=list
    )
    lookback_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    analysis_policy: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class MonitorCursorRecord(Base):
    """每 (monitor, platform) 一条增量游标，用于平台部分成功时独立提交。"""

    __tablename__ = "monitor_cursors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    monitor_id: Mapped[str] = mapped_column(
        ForeignKey("monitor_definitions.id"), index=True
    )
    platform: Mapped[str] = mapped_column(String(32))
    cursor_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    last_window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (UniqueConstraint("monitor_id", "platform"),)


class MonitorExecutionRecord(Base):
    """一次监测执行：计划时间、实际窗口、状态与各平台统计。"""

    __tablename__ = "monitor_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    monitor_id: Mapped[str] = mapped_column(
        ForeignKey("monitor_definitions.id"), index=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="scheduled", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    platform_stats: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("monitor_id", "scheduled_at"),
        UniqueConstraint("monitor_id", "idempotency_key"),
    )


class AlertRuleRecord(Base):
    """监测告警规则：五类确定性规则 + 冷却与严重级别。"""

    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    monitor_id: Mapped[str] = mapped_column(
        ForeignKey("monitor_definitions.id"), index=True
    )
    rule_type: Mapped[str] = mapped_column(String(32))
    parameters: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AlertOccurrenceRecord(Base):
    """告警出现记录：按 (rule, fingerprint, cooldown_bucket) 合并。"""

    __tablename__ = "alert_occurrences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    monitor_id: Mapped[str] = mapped_column(
        ForeignKey("monitor_definitions.id"), index=True
    )
    rule_id: Mapped[str] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    cooldown_bucket: Mapped[str] = mapped_column(String(32))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    trigger_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    evidence_refs: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    metric_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    explanation: Mapped[str] = mapped_column(Text, default="")
    acknowledged_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint("rule_id", "fingerprint", "cooldown_bucket"),
    )


class CanonicalEntityRecord(Base):
    """跨平台规范实体：账号主体、内容族、叙事等统一身份/主题。"""

    __tablename__ = "canonical_entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    canonical_name: Mapped[str] = mapped_column(String(300), index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="proposed", index=True)
    created_by: Mapped[str] = mapped_column(String(100), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (UniqueConstraint("case_id", "entity_type", "canonical_name"),)


class EntityMentionRecord(Base):
    """平台对象对规范实体的提及（文本 span、置信度、方法）。"""

    __tablename__ = "entity_mentions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("canonical_entities.id"), index=True
    )
    platform_object_type: Mapped[str] = mapped_column(String(32), default="post")
    platform_object_id: Mapped[str] = mapped_column(String(500))
    text_span: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    method: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("entity_id", "platform_object_type", "platform_object_id"),
    )


class AlignmentCandidateRecord(Base):
    """跨平台对齐候选对：特征分解、综合分、决策与算法版本。

    left_key / right_key 是排序后的规范化无向键，保证 A-B 与 B-A 不重复。
    """

    __tablename__ = "alignment_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    left_type: Mapped[str] = mapped_column(String(32))
    left_id: Mapped[str] = mapped_column(String(500))
    right_type: Mapped[str] = mapped_column(String(32))
    right_id: Mapped[str] = mapped_column(String(500))
    left_key: Mapped[str] = mapped_column(String(600))
    right_key: Mapped[str] = mapped_column(String(600))
    relation_type: Mapped[str] = mapped_column(String(32), default="same_as")
    feature_scores: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    combined_score: Mapped[float] = mapped_column(Float, default=0)
    decision: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    review_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model_version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "case_id", "left_key", "right_key", "relation_type", "model_version"
        ),
    )


class ContentFamilyRecord(Base):
    """规范内容族：同一材料的跨平台变体（原帖/搬运/剪辑）。"""

    __tablename__ = "content_families"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    label: Mapped[str] = mapped_column(String(300), default="")
    earliest_known_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ContentFamilyMemberRecord(Base):
    """内容族成员：原帖/搬运/剪辑关系、时间偏移与编辑特征。"""

    __tablename__ = "content_family_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(
        ForeignKey("content_families.id"), index=True
    )
    member_type: Mapped[str] = mapped_column(String(32), default="post")
    member_id: Mapped[str] = mapped_column(String(500))
    relation: Mapped[str] = mapped_column(String(32), default="original")
    time_offset_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    edit_features: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    decision_source: Mapped[str] = mapped_column(String(100), default="algorithm")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (UniqueConstraint("family_id", "member_id"),)


class NarrativeMembershipRecord(Base):
    """帖子/主张与叙事版本的成员关系与分数。"""

    __tablename__ = "narrative_memberships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    narrative_id: Mapped[str] = mapped_column(String(36), index=True)
    post_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_posts.id"), nullable=True, index=True
    )
    claim_id: Mapped[str | None] = mapped_column(
        ForeignKey("claims.id"), nullable=True, index=True
    )
    membership_score: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (UniqueConstraint("narrative_id", "post_id"),)


class BehaviorFeatureSnapshotRecord(Base):
    """行为特征快照：对象、窗口、特征值与数据覆盖。"""

    __tablename__ = "behavior_feature_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(32), default="account")
    subject_id: Mapped[str] = mapped_column(String(200))
    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    feature_name: Mapped[str] = mapped_column(String(64))
    feature_value: Mapped[float] = mapped_column(Float, default=0)
    coverage: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    extract_version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "case_id", "subject_type", "subject_id", "window_start", "feature_name"
        ),
    )


class RiskAssessmentRecord(Base):
    """单对象风险信号：自动化/营销/不真实三类，含原因码与证据。"""

    __tablename__ = "risk_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(32), default="account")
    subject_id: Mapped[str] = mapped_column(String(200))
    risk_type: Mapped[str] = mapped_column(String(32))
    score: Mapped[float] = mapped_column(Float, default=0)
    band: Mapped[str] = mapped_column(String(16), default="low")
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    model_version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    status: Mapped[str] = mapped_column(String(32), default="signal_only", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "case_id", "subject_type", "subject_id", "risk_type", "model_version"
        ),
    )


class CoordinationClusterRecord(Base):
    """疑似协同群体：账号—内容/链接/时间桶二部图投影的结果。"""

    __tablename__ = "coordination_clusters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    algorithm_version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0)
    explanation: Mapped[str] = mapped_column(Text, default="")
    review_status: Mapped[str] = mapped_column(
        String(32), default="signal_only", index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (UniqueConstraint("case_id", "fingerprint"),)


class CoordinationMemberRecord(Base):
    """协同群体成员：账号、成员分数、角色与证据。"""

    __tablename__ = "coordination_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cluster_id: Mapped[str] = mapped_column(
        ForeignKey("coordination_clusters.id"), index=True
    )
    account_id: Mapped[str] = mapped_column(String(200))
    membership_score: Mapped[float] = mapped_column(Float, default=0)
    role: Mapped[str] = mapped_column(String(32), default="member")
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (UniqueConstraint("cluster_id", "account_id"),)


class RiskPolicyVersionRecord(Base):
    """风险策略版本：阈值、权重、适用平台与生效时间。"""

    __tablename__ = "risk_policy_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    thresholds: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    weights: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class QualityAssessmentRecord(Base):
    """质量维度评估：覆盖/采样偏差/测量不确定/模型不确定/证据强度/稳健性。"""

    __tablename__ = "quality_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(200))
    dimension: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(32))  # high/medium/low/insufficient
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    method: Mapped[str] = mapped_column(String(100), default="")
    inputs: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    limitations: Mapped[list[str]] = mapped_column(JSON, default=list)
    version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("case_id", "target_type", "target_id", "dimension", "version"),
    )


class AnalysisAssumptionRecord(Base):
    """分析假设登记：目标、假设名、取值、来源与可编辑状态。"""

    __tablename__ = "analysis_assumptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    analysis_target: Mapped[str] = mapped_column(String(200))
    assumption_name: Mapped[str] = mapped_column(String(200))
    value: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(100), default="system")
    editable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint("case_id", "analysis_target", "assumption_name"),
    )


class SensitivityRunRecord(Base):
    """敏感性运行：基线、变体参数与输出差异，按参数哈希幂等复用。"""

    __tablename__ = "sensitivity_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    baseline_hash: Mapped[str] = mapped_column(String(64))
    baseline_params: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    variant_params: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    output_diff: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    cost: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (UniqueConstraint("case_id", "baseline_hash"),)


class AlternativeHypothesisRecord(Base):
    """可证伪的替代解释：陈述、预测、支持/反对证据与审核记录。"""

    __tablename__ = "alternative_hypotheses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    statement: Mapped[str] = mapped_column(Text)
    prediction: Mapped[str] = mapped_column(Text, default="")
    supporting_evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    opposing_evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="proposed", index=True)
    proposer: Mapped[str] = mapped_column(String(100), default="system")
    review_notes: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ConclusionConfidenceRecord(Base):
    """结论置信度：各维度分解、最终等级、禁止性原因与校准版本。"""

    __tablename__ = "conclusion_confidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    conclusion_id: Mapped[str] = mapped_column(String(200))
    conclusion_text: Mapped[str] = mapped_column(Text, default="")
    dimensions: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    final_level: Mapped[str] = mapped_column(String(32))  # high/medium/low/insufficient
    forbidden_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    calibration_version: Mapped[str] = mapped_column(String(64), default="uncalibrated")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("case_id", "conclusion_id", "calibration_version"),
    )


class AnalysisJobRecord(Base):
    """通用异步分析任务（对齐/完整性等长耗时分析，A-02）。

    analyze 入口只创建 pending 任务并返回 202，由 AnalysisJobWorker 领取
    执行；租约 + 终态 + 结果 JSON 支持可恢复与可观测。
    """

    __tablename__ = "analysis_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    progress_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint("case_id", "job_type", "idempotency_key"),
    )


class ToolPolicyVersionRecord(Base):
    """工具执行策略版本（15）：audit_only / enforce 灰度与规则快照。"""

    __tablename__ = "tool_policy_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="enforce")  # audit_only / enforce
    rules_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("version"),
    )


class ToolExecutionProfileRecord(Base):
    """工具执行画像/manifest（15）：能力清单，启动时验证。"""

    __tablename__ = "tool_execution_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tool_name: Mapped[str] = mapped_column(String(100), index=True)
    execution_class: Mapped[str] = mapped_column(
        String(24), default="trusted_in_process"
    )  # trusted_in_process / restricted_process / container
    filesystem: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    network: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    secrets: Mapped[list[str]] = mapped_column(JSON, default=list)
    resources: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    approval_policy: Mapped[str] = mapped_column(String(32), default="none")
    side_effects: Mapped[str] = mapped_column(String(64), default="none")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint("tool_name"),
    )


class SecretReferenceRecord(Base):
    """密钥引用（15）：只存引用/哈希，不存明文。"""

    __tablename__ = "secret_references"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="env")
    ref: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str] = mapped_column(String(64), default="1")
    rotation_state: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("name"),
    )


class SandboxExecutionRecord(Base):
    """沙箱执行记录（15）：每次受限 ToolCall 的隔离运行与资源计量。"""

    __tablename__ = "sandbox_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tool_call_id: Mapped[str] = mapped_column(String(100), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(100))
    execution_class: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    resource_usage: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    termination_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EgressAuditEventRecord(Base):
    """网络出口审计（15）：工具外联决策与字节计量。"""

    __tablename__ = "egress_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(String(200), default="")
    decision: Mapped[str] = mapped_column(String(16), default="deny")  # allow / deny
    reason: Mapped[str] = mapped_column(String(200), default="")
    bytes_sent: Mapped[int] = mapped_column(Integer, default=0)
    bytes_received: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LexiconEntryRecord(Base):
    """版本化领域词典条目（11）：术语/别名/谐音/拆字，时间与平台有效。"""

    __tablename__ = "lexicon_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    term: Mapped[str] = mapped_column(String(200), index=True)
    normalized: Mapped[str] = mapped_column(String(200), default="", index=True)
    meaning: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str] = mapped_column(String(64), default="general")
    platform: Mapped[str] = mapped_column(String(32), default="")
    language: Mapped[str] = mapped_column(String(16), default="zh")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(200), default="")
    # proposed/approved/rejected
    review_state: Mapped[str] = mapped_column(String(24), default="proposed")
    version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SemanticAnnotationRecord(Base):
    """语义标注（11）：source 对象上的任务级结构化输出。"""

    __tablename__ = "semantic_annotations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(32))  # post / comment / claim / document
    # sentiment / stance / irony / claim_span / entity
    source_id: Mapped[str] = mapped_column(String(200), index=True)
    # sentiment / stance / irony / claim_span / entity
    task: Mapped[str] = mapped_column(String(32), index=True)
    label: Mapped[str] = mapped_column(String(64), default="")
    span_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entity_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    provider: Mapped[str] = mapped_column(String(32), default="rules")
    model_version: Mapped[str] = mapped_column(String(64), default="")
    lexicon_version: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(24), default="active")  # active/superseded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "source_type", "source_id", "task", "label", "span_start",
            name="uq_semantic_annotation_key",
        ),
    )


class AnnotationCorrectionRecord(Base):
    """人工语义纠错（11）：原结果、修正、原因与 actor，追加写。"""

    __tablename__ = "annotation_corrections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    annotation_id: Mapped[str] = mapped_column(String(36), index=True)
    original: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    corrected: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(100), default="local_operator")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TranslationSegmentRecord(Base):
    """翻译片段（11）：仅作辅助，不替代原文证据。"""

    __tablename__ = "translation_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(String(200), index=True)
    source_span: Mapped[str] = mapped_column(Text, default="")
    source_lang: Mapped[str] = mapped_column(String(16), default="zh")
    target_lang: Mapped[str] = mapped_column(String(16), default="en")
    translated_text: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(32), default="rules")
    version: Mapped[str] = mapped_column(String(64), default="")
    quality_status: Mapped[str] = mapped_column(String(24), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SemanticModelVersionRecord(Base):
    """语义模型/词典版本（11）：能力、数据版本、阈值与状态。"""

    __tablename__ = "semantic_model_versions"
    # lexicon / classifier / provider

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # lexicon / classifier / provider
    component: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[str] = mapped_column(String(64))
    capability: Mapped[str] = mapped_column(String(64), default="")
    training_data_version: Mapped[str] = mapped_column(String(64), default="")
    eval_data_version: Mapped[str] = mapped_column(String(64), default="")
    thresholds: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("component", "version"),
    )


class NarrativeRecord(Base):
    """叙事（10）：围绕同一解释框架的一组主张和内容。"""

    __tablename__ = "narratives"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    canonical_summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="active")  # active / archived
    created_source: Mapped[str] = mapped_column(String(32), default="clusterer")
    review_state: Mapped[str] = mapped_column(String(24), default="unreviewed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class NarrativeVersionRecord(Base):
    """叙事版本（10）：一个算法版本与数据水位线下的快照，不可原地覆盖。"""

    __tablename__ = "narrative_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    narrative_id: Mapped[str] = mapped_column(ForeignKey("narratives.id"), index=True)
    data_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    algorithm_version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    centroid: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    metrics: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("narrative_id", "algorithm_version", name="uq_narrative_version"),
    )


class NarrativeClaimRecord(Base):
    """叙事-主张成员（10）。"""

    __tablename__ = "narrative_claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    narrative_id: Mapped[str] = mapped_column(ForeignKey("narratives.id"), index=True)
    claim_id: Mapped[str] = mapped_column(String(36), index=True)
    membership_score: Mapped[float] = mapped_column(Float, default=0.0)
    relation: Mapped[str] = mapped_column(String(32), default="member")
    decision_source: Mapped[str] = mapped_column(String(32), default="auto")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("narrative_id", "claim_id", name="uq_narrative_claim"),
    )


class NarrativePostRecord(Base):
    """叙事-帖子成员（10）。"""

    __tablename__ = "narrative_posts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    narrative_id: Mapped[str] = mapped_column(ForeignKey("narratives.id"), index=True)
    post_id: Mapped[str] = mapped_column(String(36), index=True)
    membership_score: Mapped[float] = mapped_column(Float, default=0.0)
    decision_source: Mapped[str] = mapped_column(String(32), default="auto")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("narrative_id", "post_id", name="uq_narrative_post"),
    )


class NarrativeTransitionRecord(Base):
    """叙事变体转换（10）：from/to variant、类型、首见时间与证据。"""

    __tablename__ = "narrative_transitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    narrative_id: Mapped[str] = mapped_column(ForeignKey("narratives.id"), index=True)
    from_variant: Mapped[str] = mapped_column(String(200), default="")
    to_variant: Mapped[str] = mapped_column(String(200), default="")
    transition_type: Mapped[str] = mapped_column(String(32), default="variant_added")
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CorrectionEventRecord(Base):
    """纠错事件（10）：明确否认/澄清/补充上下文的内容。"""

    __tablename__ = "correction_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    source_post_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_narrative_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    correction_type: Mapped[str] = mapped_column(String(32), default="clarification")
    content: Mapped[str] = mapped_column(Text, default="")
    publisher_class: Mapped[str] = mapped_column(String(32), default="unknown")
    review_state: Mapped[str] = mapped_column(String(24), default="unreviewed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LifecycleSnapshotRecord(Base):
    """生命周期快照（10）：时间桶 × 平台指标与阶段。"""

    __tablename__ = "lifecycle_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    narrative_id: Mapped[str] = mapped_column(ForeignKey("narratives.id"), index=True)
    time_bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    platform: Mapped[str] = mapped_column(String(32), default="")
    volume: Mapped[int] = mapped_column(Integer, default=0)
    unique_accounts: Mapped[int] = mapped_column(Integer, default=0)
    engagement: Mapped[int] = mapped_column(Integer, default=0)
    risk_adjusted_metrics: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    stage: Mapped[str] = mapped_column(String(24), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("narrative_id", "time_bucket", "platform", name="uq_lifecycle_snapshot"),
    )


class CorrectionImpactAnalysisRecord(Base):
    """纠错影响分析（10）：描述性前后对比，默认不声称因果。"""

    __tablename__ = "correction_impact_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    correction_event_id: Mapped[str] = mapped_column(String(36), index=True)
    narrative_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    window: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    method: Mapped[str] = mapped_column(String(64), default="descriptive")
    metrics: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    limitations: Mapped[list[str]] = mapped_column(JSON, default=list)
    result: Mapped[str] = mapped_column(String(200), default="")
    confidence_level: Mapped[str] = mapped_column(String(24), default="low")
    causal_claim: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReviewItemRecord(Base):
    """审核项（09）：统一调查对象（证据/主张/传播边/对齐/风险/假设/报告结论）。"""

    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    object_type: Mapped[str] = mapped_column(String(32), index=True)
    object_id: Mapped[str] = mapped_column(String(200), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(32), default="unreviewed", index=True
    )  # unreviewed/in_review/accepted/rejected/needs_more_evidence/superseded
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    queue: Mapped[str] = mapped_column(String(64), default="default")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint("case_id", "object_type", "object_id", name="uq_review_item_object"),
    )


class ReviewAssignmentRecord(Base):
    """审核分配（09）：actor 领取/释放。"""

    __tablename__ = "review_assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("review_items.id"), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active")  # active / released


class ReviewDecisionRecord(Base):
    """审核决策（09）：追加写；撤销/覆盖通过 supersede，禁止覆盖历史。"""

    __tablename__ = "review_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("review_items.id"), index=True)
    object_version: Mapped[int] = mapped_column(Integer, default=1)
    decision: Mapped[str] = mapped_column(
        String(32)
    )  # approved/rejected/edited_approval/more_evidence/revoked
    structured_patch: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(100), default="local_operator")
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReviewCommentRecord(Base):
    """审核评论（09）：线程、引用证据、可见性和解决状态。"""

    __tablename__ = "review_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("review_items.id"), index=True)
    thread_id: Mapped[str] = mapped_column(String(36), default="")
    reference: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="team")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    actor: Mapped[str] = mapped_column(String(100), default="local_operator")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReviewPolicyRecord(Base):
    """审核策略（09）：对象类型、风险条件、所需审核数、允许动作和超时。"""

    __tablename__ = "review_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    object_type: Mapped[str] = mapped_column(String(32), index=True)
    risk_condition: Mapped[str] = mapped_column(String(32), default="high")
    required_reviews: Mapped[int] = mapped_column(Integer, default=1)
    allowed_actions: Mapped[list[str]] = mapped_column(JSON, default=list)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=172800)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("object_type", "risk_condition", name="uq_review_policy"),
    )


class CaseActivityLogRecord(Base):
    """案件活动日志（09）：面向调查的可读审计事件。"""

    __tablename__ = "case_activity_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    activity_type: Mapped[str] = mapped_column(String(48), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(100), default="system")
    ref_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ref_tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SubscriptionRecord(Base):
    """订阅（13）：case 范围的事件订阅，含频道、计划与静默时段。"""

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    event_filters: Mapped[list[str]] = mapped_column(JSON, default=list)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    channel: Mapped[str] = mapped_column(String(32), default="inbox")  # inbox / webhook
    endpoint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schedule: Mapped[str] = mapped_column(String(24), default="instant")  # instant / daily
    quiet_hours: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class NotificationEndpointRecord(Base):
    """通知端点（13）：Webhook 等；secret 只存引用，不存明文。"""

    __tablename__ = "notification_endpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    type: Mapped[str] = mapped_column(String(32), default="webhook")
    name: Mapped[str] = mapped_column(String(200), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    secret_ref: Mapped[str] = mapped_column(String(200), default="")
    allowed_event_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    verification_state: Mapped[str] = mapped_column(String(24), default="unverified")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class NotificationEventRecord(Base):
    """领域事件 Outbox（13）：业务事务写入，Dispatcher 异步投递。"""

    __tablename__ = "notification_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    case_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    classification: Mapped[str] = mapped_column(String(32), default="monitoring")
    data: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("dedupe_key"),
    )


class DeliveryAttemptRecord(Base):
    """投递尝试（13）：每次投递的状态、HTTP 摘要、重试与死信。"""

    __tablename__ = "delivery_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    # pending/sent/failed/dead/retry_wait
    subscription_id: Mapped[str] = mapped_column(String(36), index=True)
    endpoint_id: Mapped[str] = mapped_column(String(36), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    # pending/sent/failed/dead/retry_wait
    status: Mapped[str] = mapped_column(String(24), default="pending")
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_summary: Mapped[str] = mapped_column(String(200), default="")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint("event_id", "subscription_id", name="uq_delivery_event_sub"),
    )


class DigestBatchRecord(Base):
    """摘要批次（13）：窗口内事件集合、状态与产物。"""

    __tablename__ = "digest_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subscription_id: Mapped[str] = mapped_column(String(36), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("subscription_id", "window_start", name="uq_digest_window"),
    )


class ShareLinkRecord(Base):
    """只读分享链接（13）：随机 token 只存哈希，可过期/撤销/限额。"""

    __tablename__ = "share_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(32), default="artifact")  # artifact / case
    target_id: Mapped[str] = mapped_column(String(200), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    download_limit: Mapped[int] = mapped_column(Integer, default=0)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    download_window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    download_window_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ExportJobRecord(Base):
    """导出任务（13）：范围、格式、脱敏策略、状态与产物。"""

    __tablename__ = "export_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    scope: Mapped[str] = mapped_column(String(32), default="case")  # case / evidence / artifact
    scope_ref: Mapped[str] = mapped_column(String(200), default="")
    format: Mapped[str] = mapped_column(String(16), default="json")  # json / csv / markdown
    redaction_policy: Mapped[str] = mapped_column(String(32), default="standard")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ContentSecurityAssessmentRecord(Base):
    """内容安全评估（16）：对象级风险信号、评分与处置。"""

    __tablename__ = "content_security_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    object_type: Mapped[str] = mapped_column(String(32), default="content")
    object_id: Mapped[str] = mapped_column(String(200), default="")
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    trust_level: Mapped[str] = mapped_column(String(32), default="external_content")
    # allowed/isolated/truncated/quarantined/blocked
    classification: Mapped[str] = mapped_column(String(64), default="general")
    score: Mapped[float] = mapped_column(Float, default=0)
    risk_signals: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    detector: Mapped[str] = mapped_column(String(100), default="")
    detector_version: Mapped[str] = mapped_column(String(32), default="1.0")
    # allowed/isolated/truncated/quarantined/blocked
    disposition: Mapped[str] = mapped_column(String(32), default="allowed")
    reason: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    source_type: Mapped[str] = mapped_column(String(64), default="")
    review_state: Mapped[str] = mapped_column(String(32), default="unreviewed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


    # tool_input/tool_output/context/memory_write/structured_output
class GuardrailDecisionRecord(Base):
    """护栏决策（16）：run/turn/tool 各级 stage 的决策、理由与策略版本。"""

    __tablename__ = "guardrail_decisions"
    # allow/deny/isolate/truncate/require_approval

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # tool_input/tool_output/context/memory_write/structured_output
    stage: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    tool: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # allow/deny/isolate/truncate/require_approval
    decision: Mapped[str] = mapped_column(String(32), default="allow")
    reason: Mapped[str] = mapped_column(Text, default="")
    policy_version: Mapped[str] = mapped_column(String(32), default="1.0")
    signal_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class GoalRecord(Base):
    """显式目标（17）：范围、约束、优先级与版本。"""

    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    objective: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    constraints: Mapped[list[str]] = mapped_column(JSON, default=list)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(32), default="user")
    cancelled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AcceptanceCriterionRecord(Base):
    """验收标准（17）：结构化判定类型、期望值与状态。"""

    __tablename__ = "acceptance_criteria"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    goal_id: Mapped[str] = mapped_column(ForeignKey("goals.id"), index=True)
    criterion_type: Mapped[str] = mapped_column(String(32), default="artifact_exists")
    description: Mapped[str] = mapped_column(Text, default="")
    target: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    evidence_requirement: Mapped[str] = mapped_column(String(32), default="required")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PlanVersionRecord(Base):
    """计划图版本（17）：DAG 快照，修改生成新版本不覆盖历史。"""

    __tablename__ = "plan_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    goal_id: Mapped[str] = mapped_column(ForeignKey("goals.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    planner: Mapped[str] = mapped_column(String(32), default="deterministic")
    frozen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("goal_id", "version", name="uq_plan_version_goal"),
    )


class PlanStepRecord(Base):
    """计划步骤（17）：任务、能力、预算、状态与完成声明。"""

    __tablename__ = "plan_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plan_version_id: Mapped[str] = mapped_column(
        ForeignKey("plan_versions.id"), index=True
    )
    step_key: Mapped[str] = mapped_column(String(100), default="")
    task: Mapped[str] = mapped_column(Text, default="")
    agent_capability: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    budget_max_cost: Mapped[float] = mapped_column(Float, default=5)
    max_turns: Mapped[int] = mapped_column(Integer, default=16)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    declared_by: Mapped[str] = mapped_column(String(32), default="planner")
    completion_declared_by: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PlanEdgeRecord(Base):
    """计划边（17）：步骤间依赖/数据流，DAG 必须无环。"""

    __tablename__ = "plan_edges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plan_version_id: Mapped[str] = mapped_column(
        ForeignKey("plan_versions.id"), index=True
    )
    source_step_key: Mapped[str] = mapped_column(String(100), default="")
    target_step_key: Mapped[str] = mapped_column(String(100), default="")
    edge_type: Mapped[str] = mapped_column(String(24), default="dependency")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "plan_version_id",
            "source_step_key",
            "target_step_key",
            name="uq_plan_edge",
        ),
    )


class StepEvidenceRecord(Base):
    """步骤证据（17）：Artifact、ToolCall、测试结果、人工决策等引用。"""

    __tablename__ = "step_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    step_id: Mapped[str] = mapped_column(ForeignKey("plan_steps.id"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(32), default="artifact")
    ref_id: Mapped[str] = mapped_column(String(200), default="")
    ref_kind: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CompletionAssessmentRecord(Base):
    """完成评估（17）：独立验证器结果、逐标准结果与缺口。"""

    __tablename__ = "completion_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    goal_id: Mapped[str] = mapped_column(ForeignKey("goals.id"), index=True)
    plan_version_id: Mapped[str] = mapped_column(ForeignKey("plan_versions.id"))
    verifier: Mapped[str] = mapped_column(String(32), default="deterministic")
    result: Mapped[str] = mapped_column(String(32), default="insufficient_evidence")
    criterion_results: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    gaps: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "goal_id",
            "plan_version_id",
            name="uq_assessment_goal_plan",
        ),
    )


class DatasetManifestRecord(Base):
    """评测数据集清单（20）：名称/版本/任务/许可/hash/盲测标记。"""

    __tablename__ = "dataset_manifests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    task: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(64), default="")
    license: Mapped[str] = mapped_column(String(64), default="")
    time_range: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    example_count: Mapped[int] = mapped_column(Integer, default=0)
    train_holdout: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_manifest_name_version"),
    )


class DatasetExampleRecord(Base):
    """评测样例（20）：输入引用、金标、标注分歧、禁止训练标记。"""

    __tablename__ = "dataset_examples"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    manifest_id: Mapped[str] = mapped_column(ForeignKey("dataset_manifests.id"), index=True)
    example_id: Mapped[str] = mapped_column(String(100), default="")
    task: Mapped[str] = mapped_column(String(64), default="")
    input_ref: Mapped[str] = mapped_column(String(500), default="")
    input_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    gold: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    difficulty: Mapped[str] = mapped_column(String(16), default="normal")
    label_disagreement: Mapped[bool] = mapped_column(Boolean, default=False)
    training_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("manifest_id", "example_id", name="uq_example_manifest_id"),
    )


class EvaluatorDefinitionRecord(Base):
    """评测器定义（20）：指标、确定性、阈值与依赖。"""

    __tablename__ = "evaluator_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    metric: Mapped[str] = mapped_column(String(100), default="")
    deterministic: Mapped[bool] = mapped_column(Boolean, default=True)
    thresholds: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    dependencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvaluationRunRecord(Base):
    """评测运行（20）：candidate/baseline/数据集/环境/结果/差异。"""

    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    suite: Mapped[str] = mapped_column(String(64), index=True)
    candidate_version: Mapped[str] = mapped_column(String(100), default="")
    baseline_version: Mapped[str] = mapped_column(String(100), default="")
    dataset_manifest_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_manifests.id"), index=True
    )
    commit: Mapped[str] = mapped_column(String(64), default="")
    config: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    environment: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    results: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    aggregate: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    differences: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    error_samples: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReleaseGateRecord(Base):
    """发布门禁（20）：绝对阈值 + 相对回归限制 + 强制/豁免。"""

    __tablename__ = "release_gates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100), index=True)
    suite: Mapped[str] = mapped_column(String(64), default="")
    thresholds: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    relative_regression_limits: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict
    )
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class EvaluationGateResultRecord(Base):
    """门禁判定结果（20）：pass/block + 豁免记录。"""

    __tablename__ = "evaluation_gate_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    gate_id: Mapped[str] = mapped_column(ForeignKey("release_gates.id"), index=True)
    evaluation_run_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_runs.id"), index=True
    )
    decision: Mapped[str] = mapped_column(String(16), default="pending")
    reason: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    exempted_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    exempt_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    exempt_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("gate_id", "evaluation_run_id", name="uq_gate_run"),
    )

class DependencyHealthRecord(Base):
    """依赖健康（22）：每个依赖独立健康状态、熔断状态与最近成败。"""

    __tablename__ = "dependency_health"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dependency: Mapped[str] = mapped_column(String(120), index=True)
    scope: Mapped[str] = mapped_column(String(64), default="")
    # healthy / degraded / outage / auth_required / policy_denied
    status: Mapped[str] = mapped_column(String(24), default="healthy")
    error_code: Mapped[str] = mapped_column(String(100), default="")
    # closed / open / half_open
    circuit_state: Mapped[str] = mapped_column(String(24), default="closed")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (UniqueConstraint("dependency", "scope"),)


class CircuitBreakerStateRecord(Base):
    """熔断状态（22）：滚动窗口失败率 + 半开探测，按依赖隔离持久化。"""

    __tablename__ = "circuit_breaker_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dependency: Mapped[str] = mapped_column(String(120), index=True)
    scope: Mapped[str] = mapped_column(String(64), default="")
    # closed / open / half_open
    state: Mapped[str] = mapped_column(String(24), default="closed")
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    half_open_probe_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    config_version: Mapped[str] = mapped_column(String(32), default="1.0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (UniqueConstraint("dependency", "scope"),)


class RetryAttemptRecord(Base):
    """重试记录（22）：操作幂等键 + 错误分类 + 尝试链，保留首次错误。"""

    __tablename__ = "retry_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operation_key: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    dependency: Mapped[str] = mapped_column(String(120), default="")
    scope: Mapped[str] = mapped_column(String(64), default="")
    # transient / rate_limited / auth_required / permanent_input /
    # policy_denied / resource_exhausted / dependency_outage / unknown
    error_classification: Mapped[str] = mapped_column(String(32), default="unknown")
    error_code: Mapped[str] = mapped_column(String(100), default="")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    backoff_seconds: Mapped[float] = mapped_column(Float, default=0)
    retry_after_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # pending / succeeded / failed / permanent / dead_lettered
    status: Mapped[str] = mapped_column(String(24), default="pending")
    first_error: Mapped[str] = mapped_column(Text, default="")
    first_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DeadLetterItemRecord(Base):
    """死信（22）：任务引用 + 错误分类 + payload 哈希 + 恢复建议。

    敏感 payload 只保存引用（payload_ref），不落原文。
    """

    __tablename__ = "dead_letter_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operation_key: Mapped[str] = mapped_column(String(500), index=True)
    dependency: Mapped[str] = mapped_column(String(120), default="")
    scope: Mapped[str] = mapped_column(String(64), default="")
    error_classification: Mapped[str] = mapped_column(String(32), default="unknown")
    error_code: Mapped[str] = mapped_column(String(100), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    policy_version: Mapped[str] = mapped_column(String(32), default="")
    code_version: Mapped[str] = mapped_column(String(64), default="")
    recovery_hint: Mapped[str] = mapped_column(Text, default="")
    payload_ref: Mapped[str] = mapped_column(String(500), default="")
    # pending / approved / retrying / resolved / discarded
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IncidentRecord(Base):
    """事故记录（22）：影响范围、时间线、指标、处置、恢复与复盘。

    只记录指标与处置摘要，不含密钥。
    """

    __tablename__ = "incident_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300), default="")
    # info / warning / critical
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    # open / closed
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    impact: Mapped[str] = mapped_column(Text, default="")
    timeline_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    metrics_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    actions_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    recovery_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    retro_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    kill_switch_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class KillSwitchRecord(Base):
    """Kill Switch（22）：全局/按平台/按工具停止，操作审计，可审计解除。"""

    __tablename__ = "kill_switches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # global / platform / tool / dependency
    scope: Mapped[str] = mapped_column(String(24), default="global", index=True)
    target: Mapped[str] = mapped_column(String(120), default="*")
    # on / off
    status: Mapped[str] = mapped_column(String(8), default="off")
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(100), default="")
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (UniqueConstraint("scope", "target"),)


class MemoryAccessEventRecord(Base):
    """记忆访问事件（23）：读取用途、run、结果数量，仅存摘要。"""

    __tablename__ = "memory_access_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    purpose: Mapped[str] = mapped_column(String(64), default="")
    result_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryMutationRecord(Base):
    """记忆变更（23）：创建/修正/停用/删除/恢复/审核，可审计。"""

    __tablename__ = "memory_mutations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), index=True)
    # create / correct / disable / restore / delete / review / reindex / expire
    action: Mapped[str] = mapped_column(String(24), default="create")
    actor: Mapped[str] = mapped_column(String(100), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    from_status: Mapped[str] = mapped_column(String(24), default="")
    to_status: Mapped[str] = mapped_column(String(24), default="")
    version_before: Mapped[int] = mapped_column(Integer, default=0)
    version_after: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryConflictRecord(Base):
    """记忆冲突（23）：矛盾内容成对记录，不静默覆盖。"""

    __tablename__ = "memory_conflicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), index=True)
    conflicting_memory_id: Mapped[str] = mapped_column(
        ForeignKey("memories.id"), index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolution: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (UniqueConstraint("memory_id", "conflicting_memory_id"),)


class CollectionDefinitionRecord(Base):
    """M3：显式采集定义（版本化）。

    每个调查可拥有多个版本，至多一个 active；激活新版本时旧 active 由
    service 事务置为 superseded。版本不可变，修改只能产生新版本。
    """

    __tablename__ = "collection_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    platforms: Mapped[list[Any]] = mapped_column(_Utf8JSON, default=list)
    platform_queries: Mapped[dict[str, Any]] = mapped_column(_Utf8JSON, default=dict)
    exclusions: Mapped[list[Any]] = mapped_column(_Utf8JSON, default=list)
    filters: Mapped[dict[str, Any]] = mapped_column(_Utf8JSON, default=dict)
    generated_by_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint("case_id", "version", name="uq_collection_case_version"),
        Index(
            "uq_collection_case_active",
            "case_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )


class FindingRecord(Base):
    """M4：调查结论对象（用户可审核/接受/拒绝/追溯证据的稳定 Finding）。

    Agent 产出的 Artifact 经 deterministic materializer 创建为 candidate；
    verified/rejected 只能来自 Review 决策（ReviewService 是事实来源）。
    """

    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(_Utf8JSON, default=dict)
    source_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        Index("ix_findings_case_status", "case_id", "status"),
        Index("ix_findings_case_kind", "case_id", "kind"),
    )


class FindingEvidenceLinkRecord(Base):
    """M4：Finding ↔ Evidence 关联（supports/contradicts/context）。"""

    __tablename__ = "finding_evidence_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    finding_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("findings.id"), index=True, nullable=False
    )
    evidence_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "finding_id", "evidence_ref", "relation", name="uq_finding_evidence"
        ),
    )


class FindingSourceLinkRecord(Base):
    """M4：Finding 来源链接（幂等键：重复 sync 不创建重复 Finding）。"""

    __tablename__ = "finding_source_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    finding_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("findings.id"), index=True, nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_path: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "source_type", "source_id", "source_path", name="uq_finding_source"
        ),
    )
