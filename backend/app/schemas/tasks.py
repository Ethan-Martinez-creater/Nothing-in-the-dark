from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StartAnalysisRequest(BaseModel):
    force_crawl: bool = False
    include_fact_check: bool = True
    max_budget: float = Field(default=5.0, gt=0, le=100)


class TaskResponse(BaseModel):
    id: str
    case_id: str
    status: str
    current_stage: str
    progress: float
    options: dict[str, object]
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskEventResponse(BaseModel):
    id: int
    task_id: str
    event_type: str
    stage: str
    message: str
    progress: float
    payload: dict[str, object]
    created_at: datetime

    model_config = {"from_attributes": True}


class ArtifactResponse(BaseModel):
    id: str
    case_id: str
    task_id: str | None
    kind: str
    title: str
    version: int
    data: dict[str, object]
    created_at: datetime
    run_id: str | None = None

    model_config = {"from_attributes": True}

