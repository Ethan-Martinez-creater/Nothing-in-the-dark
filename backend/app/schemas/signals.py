"""M6: Global Signals API 契约（Monitor Alert adapter，无新表）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

SIGNAL_TYPE_BY_RULE = {
    "absolute_volume": "volume_spike",
    "rate_growth": "growth_spike",
    "anomaly": "anomaly",
    "key_account": "key_actor",
    "narrative": "narrative_shift",
}

SIGNAL_TITLE_BY_TYPE = {
    "volume_spike": "讨论量达到告警阈值",
    "growth_spike": "讨论增长速度异常",
    "anomaly": "检测到异常活动",
    "key_actor": "重点账号触发监测规则",
    "narrative_shift": "检测到叙事变化",
}

# V3 §59：Derived Signal source label（前端 Source filter）
SOURCE_LABELS = {
    "coordination_cluster": "Coordination",
    "actor_recurrence": "Actor recurrence",
    "media_reuse": "Media reuse",
    "cross_case_overlap": "Cross-case overlap",
}


class SignalResponse(BaseModel):
    id: str
    source_type: str
    source_id: str
    case_id: str
    case_title: str
    signal_type: str
    severity: str
    status: str
    title: str
    why_it_matters: str
    confidence: float | None = None
    evidence_refs: dict[str, Any]
    trigger_count: int = 1
    first_seen_at: datetime | None = None
    detected_at: datetime
    updated_at: datetime
    # V3 §57 additive 字段（Monitor 信号保持默认值，兼容旧消费者）
    related_case_ids: list[str] = Field(default_factory=list)
    source_label: str = "Monitor"
    detector_version: str | None = None
    detector_active: bool | None = None
