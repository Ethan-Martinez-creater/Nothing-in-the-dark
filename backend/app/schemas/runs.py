from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class UiContext(BaseModel):
    """Copilot 发送消息时的结构化界面导航上下文。

    只是导航提示，不构成事实证据；事实内容 Agent 必须通过工具查询。
    """

    workspace: Literal[
        "overview",
        "live_data",
        "evidence",
        "network",
        "timeline",
        "findings",
        "report",
        "activity",
    ]
    selected_type: str | None = Field(default=None, max_length=100)
    selected_id: str | None = Field(default=None, max_length=200)
    selected_label: str | None = Field(default=None, max_length=500)
    filters: dict[str, Any] = Field(default_factory=dict)
    time_range: dict[str, str | None] | None = None


class CreateMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    approve_crawl: bool = False
    # M2 Artifact 追问：目标 artifact id，服务端把该 Artifact 数据注入上下文
    artifact_id: str | None = None
    # M2.2 Contextual Copilot：结构化 UI 上下文，进入 Run metadata，
    # 由 ContextBuilder 生成为独立 system context block。
    ui_context: UiContext | None = None


class SteeringRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2_000)


class SteeringResponse(BaseModel):
    id: str
    run_id: str
    content: str
    created_at: datetime
    consumed_at: datetime | None

    model_config = {"from_attributes": True}


class AgentRunResponse(BaseModel):
    id: str
    case_id: str
    turn_id: str | None
    parent_run_id: str | None
    agent: str
    status: str
    objective: str
    model_route: str
    input_tokens: int
    output_tokens: int
    tool_call_count: int
    estimated_cost: float
    error_code: str | None
    error: str | None
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RunEventResponse(BaseModel):
    id: int
    run_id: str
    event_type: str
    agent: str
    skill: str | None
    tool_call_id: str | None
    tool: str | None
    status: str
    payload: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class ApproveRequest(BaseModel):
    approval_id: str
    decision: bool = True
    note: str | None = None


class ModelCallTrace(BaseModel):
    id: str
    run_id: str
    model: str
    route: str
    status: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    estimated_cost: float
    currency: str
    pricing_model: str | None
    latency_ms: int
    error_code: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ToolCallTrace(BaseModel):
    id: str
    run_id: str
    tool_name: str
    skill_name: str | None
    status: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    error_code: str | None
    input_summary: str | None
    output_summary: str | None
    retry_count: int
    retry_history: list[dict[str, Any]] = Field(default_factory=list)
    cached: bool = False
    duration_ms: int
    estimated_cost: float
    idempotency_key: str | None
    approval_id: str | None
    # M8c: RAG hit summary {available, hit_count, retrieval_modes}; None for
    # non-retrieval tools
    rag: dict[str, Any] | None = None
    started_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class ApprovalTrace(BaseModel):
    id: str
    run_id: str
    action: str
    reason: str
    status: str
    request_payload: dict[str, Any]
    decision_payload: dict[str, Any]
    decided_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RunTraceResponse(BaseModel):
    run: AgentRunResponse
    model_calls: list[ModelCallTrace]
    tool_calls: list[ToolCallTrace]
    approvals: list[ApprovalTrace]
    events: list[RunEventResponse]
    # Cost accumulation: model calls plus tool calls, and their sum.
    model_cost_total: float = 0
    tool_cost_total: float = 0
    total_cost: float = 0
