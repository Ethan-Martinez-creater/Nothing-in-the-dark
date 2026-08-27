from __future__ import annotations

from typing import Any, TypedDict


class AnalysisState(TypedDict, total=False):
    task_id: str
    case_id: str
    topic: str
    platforms: list[str]
    time_range: dict[str, str | None]
    options: dict[str, Any]
    is_demo: bool
    plan: dict[str, Any]
    posts: list[dict[str, Any]]
    opinion: dict[str, Any]
    propagation: dict[str, Any]
    fact_check: dict[str, Any]
    report: dict[str, Any]
