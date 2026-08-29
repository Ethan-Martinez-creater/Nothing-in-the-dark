"""M6: Workspace Overview API 契约（Home 聚合，禁止 N+1）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class WorkspaceCounts(BaseModel):
    investigations: int
    open_signals: int
    pending_approvals: int
    running_runs: int


class RecentInvestigation(BaseModel):
    id: str
    title: str
    topic: str
    platforms: list[str]
    status: str
    updated_at: str


class TopSignal(BaseModel):
    id: str
    signal_type: str
    severity: str
    status: str
    title: str
    why_it_matters: str
    case_id: str
    case_title: str
    detected_at: str


class RecentReport(BaseModel):
    artifact_id: str
    case_id: str
    title: str
    created_at: str


class WorkspaceOverviewResponse(BaseModel):
    counts: WorkspaceCounts
    recent_investigations: list[RecentInvestigation]
    top_signals: list[TopSignal]
    recent_reports: list[RecentReport]


def counts_payload(**kwargs: Any) -> WorkspaceCounts:
    return WorkspaceCounts(**kwargs)
