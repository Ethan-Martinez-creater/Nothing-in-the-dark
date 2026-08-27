from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.runs import (
    AgentRunResponse,
    ApprovalTrace,
    ApproveRequest,
    ModelCallTrace,
    RunEventResponse,
    RunTraceResponse,
    SteeringRequest,
    SteeringResponse,
    ToolCallTrace,
)

router = APIRouter()


@router.get("/{run_id}", response_model=AgentRunResponse)
async def get_run(
    run_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> AgentRunResponse:
    record = await container.repository.get_agent_run(run_id)
    return AgentRunResponse.model_validate(record)


@router.post("/{run_id}/steering", response_model=SteeringResponse)
async def steer_run(
    run_id: str,
    request: SteeringRequest,
    container: ApplicationContainer = Depends(get_container),
) -> SteeringResponse:
    """Inject a steering instruction into a running coordinator run.

    The instruction is enqueued and folded into the agent loop at the next
    model step (``steering_step`` node); a ``steering_received`` event is
    emitted immediately and ``steering_applied`` once the worker consumes it.
    """
    record = await container.agent_service.steer(run_id, request.content)
    return SteeringResponse.model_validate(record)


@router.post("/{run_id}/cancel", response_model=AgentRunResponse)
async def cancel_run(
    run_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> AgentRunResponse:
    record = await container.agent_service.cancel(run_id)
    return AgentRunResponse.model_validate(record)


@router.get("/{run_id}/events", response_model=list[RunEventResponse])
async def list_run_events(
    run_id: str,
    after_id: int = Query(default=0, ge=0),
    container: ApplicationContainer = Depends(get_container),
) -> list[RunEventResponse]:
    records = await container.repository.list_run_events(run_id, after_id=after_id)
    return [RunEventResponse.model_validate(record) for record in records]


@router.get("/{run_id}/events/stream")
async def stream_run_events(
    run_id: str,
    request: Request,
    cursor: int = Query(default=0, ge=0),
    container: ApplicationContainer = Depends(get_container),
) -> StreamingResponse:
    await container.repository.get_agent_run(run_id)

    last_event_id = request.headers.get("last-event-id")
    if last_event_id and last_event_id.isdigit():
        cursor = max(cursor, int(last_event_id))

    async def event_source() -> AsyncIterator[str]:
        nonlocal cursor
        while True:
            records = await container.repository.list_run_events(
                run_id, after_id=cursor
            )
            for record in records:
                cursor = record.id
                event = RunEventResponse.model_validate(record)
                yield f"id: {event.id}\ndata: {event.model_dump_json()}\n\n"
            run = await container.repository.get_agent_run(run_id)
            if run.status in {"completed", "failed", "cancelled"} and not records:
                break
            await asyncio.sleep(container.settings.event_poll_interval_seconds)

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.post("/{run_id}/approve", response_model=AgentRunResponse)
async def approve_run(
    run_id: str,
    request: ApproveRequest,
    container: ApplicationContainer = Depends(get_container),
) -> AgentRunResponse:
    record = await container.agent_service.approve(
        run_id,
        approval_id=request.approval_id,
        decision=request.decision,
        note=request.note,
    )
    return AgentRunResponse.model_validate(record)


@router.post("/{run_id}/resume", response_model=AgentRunResponse)
async def resume_run(
    run_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> AgentRunResponse:
    record = await container.agent_service.resume(run_id)
    return AgentRunResponse.model_validate(record)


@router.get("/{run_id}/trace", response_model=RunTraceResponse)
async def get_run_trace(
    run_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> RunTraceResponse:
    trace = await container.repository.get_run_trace(run_id)
    model_calls = [
        ModelCallTrace.model_validate(record) for record in trace["model_calls"]
    ]
    tool_calls = [
        ToolCallTrace.model_validate(record) for record in trace["tool_calls"]
    ]
    model_cost_total = sum(record.estimated_cost for record in model_calls)
    tool_cost_total = sum(record.estimated_cost for record in tool_calls)
    return RunTraceResponse(
        run=AgentRunResponse.model_validate(trace["run"]),
        model_calls=model_calls,
        tool_calls=tool_calls,
        approvals=[
            ApprovalTrace.model_validate(record) for record in trace["approvals"]
        ],
        events=[RunEventResponse.model_validate(record) for record in trace["events"]],
        model_cost_total=model_cost_total,
        tool_cost_total=tool_cost_total,
        total_cost=model_cost_total + tool_cost_total,
    )
