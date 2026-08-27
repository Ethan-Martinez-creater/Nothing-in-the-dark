"""Continuous monitoring API contracts (01)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MonitorCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    schedule_type: str = Field(default="interval", pattern="^(interval|cron)$")
    interval_seconds: int | None = Field(default=3600, gt=0)
    cron: str | None = None
    timezone: str = "Asia/Shanghai"
    query_spec: dict[str, Any] = Field(default_factory=dict)
    platforms: list[str] = Field(default_factory=list)
    account_watchlist: list[dict[str, Any]] = Field(default_factory=list)
    lookback_seconds: int = Field(default=3600, ge=0)
    analysis_policy: dict[str, Any] = Field(default_factory=dict)


class MonitorUpdateRequest(BaseModel):
    version: int
    name: str | None = None
    schedule_type: str | None = Field(default=None, pattern="^(interval|cron)$")
    interval_seconds: int | None = Field(default=None, gt=0)
    cron: str | None = None
    timezone: str | None = None
    query_spec: dict[str, Any] | None = None
    platforms: list[str] | None = None
    account_watchlist: list[dict[str, Any]] | None = None
    lookback_seconds: int | None = Field(default=None, ge=0)
    analysis_policy: dict[str, Any] | None = None


class MonitorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    name: str
    enabled: bool
    schedule_type: str
    interval_seconds: int | None
    cron: str | None
    timezone: str
    query_spec: dict[str, Any]
    platforms: list[str]
    account_watchlist: list[dict[str, Any]]
    lookback_seconds: int
    analysis_policy: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime


class RunNowRequest(BaseModel):
    idempotency_key: str | None = None


class RuleCreateRequest(BaseModel):
    rule_type: str = Field(
        description="absolute_volume|rate_growth|anomaly|key_account|narrative"
    )
    parameters: dict[str, Any] = Field(default_factory=dict)
    severity: str = Field(default="warning", pattern="^(info|warning|critical)$")
    cooldown_seconds: int = Field(default=3600, ge=0)
    enabled: bool = True


class RuleUpdateRequest(BaseModel):
    version: int
    parameters: dict[str, Any] | None = None
    severity: str | None = Field(default=None, pattern="^(info|warning|critical)$")
    cooldown_seconds: int | None = Field(default=None, ge=0)
    enabled: bool | None = None


class RuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    monitor_id: str
    rule_type: str
    parameters: dict[str, Any]
    severity: str
    cooldown_seconds: int
    enabled: bool
    version: int
    created_at: datetime
    updated_at: datetime


class ExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    monitor_id: str
    scheduled_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    window_start: datetime | None
    window_end: datetime | None
    status: str
    run_id: str | None
    platform_stats: dict[str, Any]
    error_code: str | None
    next_retry_at: datetime | None
    created_at: datetime


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    monitor_id: str
    rule_id: str
    fingerprint: str
    cooldown_bucket: str
    first_seen_at: datetime
    last_seen_at: datetime
    trigger_count: int
    status: str
    evidence_refs: dict[str, Any]
    metric_snapshot: dict[str, Any]
    explanation: str
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AlertStatusRequest(BaseModel):
    by: str | None = Field(default=None, max_length=100)
