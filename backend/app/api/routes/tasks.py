from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.domain.enums import TaskStatus
from app.schemas.tasks import TaskEventResponse, TaskResponse

router = APIRouter()


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> TaskResponse:
    record = await container.repository.get_task(task_id)
    return TaskResponse.model_validate(record)


@router.get("/{task_id}/events", response_model=list[TaskEventResponse])
async def list_task_events(
    task_id: str,
    after_id: int = Query(default=0, ge=0),
    container: ApplicationContainer = Depends(get_container),
) -> list[TaskEventResponse]:
    records = await container.repository.list_events(task_id, after_id=after_id)
    return [TaskEventResponse.model_validate(record) for record in records]


@router.get("/{task_id}/events/stream")
async def stream_task_events(
    task_id: str,
    after_id: int = Query(default=0, ge=0),
    container: ApplicationContainer = Depends(get_container),
) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        cursor = after_id
        while True:
            events = await container.repository.list_events(task_id, after_id=cursor)
            for record in events:
                event = TaskEventResponse.model_validate(record)
                cursor = event.id
                yield (
                    f"id: {event.id}\n"
                    f"event: {event.event_type}\n"
                    f"data: {event.model_dump_json()}\n\n"
                )

            task = await container.repository.get_task(task_id)
            if task.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                yield (
                    "event: close\n"
                    f"data: {json.dumps({'status': task.status})}\n\n"
                )
                break
            await asyncio.sleep(container.settings.event_poll_interval_seconds)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
