"""09 分层人工调查与裁决工作台 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ApplicationError
from app.services.review import ReviewStateError

router = APIRouter()


class SubmitItemRequest(BaseModel):
    object_type: str
    object_id: str
    summary: str = ""
    priority: int = 0
    risk_level: str = "low"
    queue: str = "default"


class DecideRequest(BaseModel):
    decision: str
    reason: str = ""
    actor: str = "local_operator"
    structured_patch: dict[str, object] | None = None
    # RH2: optional for backward compatibility; first-party Review Workbench
    # always sends expected_version. The repository resolves the effective
    # version (client-provided or current snapshot) and the database CAS is
    # the final arbitration.
    expected_version: int | None = Field(default=None, ge=1)


class CommentCreate(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    actor: str = "local_operator"
    reference: str = ""
    thread_id: str = ""


@router.post("/{case_id}/reviews/items", status_code=201)
async def submit_review_item(
    case_id: str,
    request: SubmitItemRequest,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    item = await container.review_service.submit_item(
        case_id=case_id,
        object_type=request.object_type,
        object_id=request.object_id,
        summary=request.summary,
        priority=request.priority,
        risk_level=request.risk_level,
        queue=request.queue,
    )
    return _item_summary(item)


@router.get("/{case_id}/reviews/queue")
async def review_queue(
    case_id: str,
    status: str | None = Query(default=None),
    object_type: str | None = Query(default=None),
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    items = await container.review_service.list_queue(
        case_id=case_id, status=status, object_type=object_type
    )
    return {"total": len(items), "items": items}


@router.post("/{case_id}/reviews/{item_id}:claim")
async def claim_item(
    case_id: str,
    item_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    item = await container.review_service.claim(item_id, "local_operator", case_id=case_id)
    return _item_summary(item)


@router.post("/{case_id}/reviews/{item_id}:release")
async def release_item(
    case_id: str,
    item_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    item = await container.review_service.release(item_id, "local_operator", case_id=case_id)
    return _item_summary(item)


@router.post("/{case_id}/reviews/{item_id}/decisions")
async def decide_item(
    case_id: str,
    item_id: str,
    request: DecideRequest,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    try:
        item = await container.review_service.decide(
            item_id=item_id,
            decision=request.decision,
            reason=request.reason,
            actor=request.actor,
            structured_patch=request.structured_patch,
            case_id=case_id,
            expected_version=request.expected_version,
        )
    except ReviewStateError as exc:
        raise ApplicationError(str(exc), code="review_invalid_transition") from exc
    return _item_summary(item)


@router.post("/{case_id}/reviews/{item_id}:reopen")
async def reopen_item(
    case_id: str,
    item_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    item = await container.review_service.reopen(item_id, case_id=case_id)
    return _item_summary(item)


@router.get("/{case_id}/reviews/{item_id}/comments")
async def list_comments(
    case_id: str,
    item_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    await container.review_service._require_case_item(case_id, item_id)
    records = await container.repository.list_review_comments(item_id)
    return {
        "comments": [
            {
                "id": c.id,
                "thread_id": c.thread_id,
                "reference": c.reference,
                "text": c.text,
                "actor": c.actor,
                "resolved": c.resolved,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in records
        ]
    }


@router.post("/{case_id}/reviews/{item_id}/comments", status_code=201)
async def add_comment(
    case_id: str,
    item_id: str,
    request: CommentCreate,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    comment = await container.review_service.add_comment(
        item_id=item_id,
        text=request.text,
        actor=request.actor,
        reference=request.reference,
        thread_id=request.thread_id,
        case_id=case_id,
    )
    return {
        "id": comment.id,
        "item_id": comment.item_id,
        "text": comment.text,
        "actor": comment.actor,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }


@router.get("/{case_id}/activity")
async def case_activity(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    records = await container.repository.list_activity_log(case_id)
    return {
        "events": [
            {
                "id": r.id,
                "activity_type": r.activity_type,
                "summary": r.summary,
                "actor": r.actor,
                "ref_run_id": r.ref_run_id,
                "metadata": r.metadata_json,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]
    }


def _item_summary(item: object) -> dict[str, object]:
    return {
        "id": item.id,
        "case_id": item.case_id,
        "object_type": item.object_type,
        "object_id": item.object_id,
        "priority": item.priority,
        "status": item.status,
        "risk_level": item.risk_level,
        "queue": item.queue,
        "summary": item.summary,
        "current_version": item.current_version,
    }
