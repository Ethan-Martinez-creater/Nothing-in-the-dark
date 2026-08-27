"""M21: generalized human-in-the-loop approval inbox API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ApplicationError

router = APIRouter()

_DECISIONS = frozenset({"approve", "edit_and_approve", "reject", "cancel"})


@router.get("")
async def list_approvals(
    case_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    approval_type: str | None = Query(default=None),
    risk_level: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    records = await container.repository.list_approvals(
        case_id=case_id,
        run_id=run_id,
        status=status,
        approval_type=approval_type,
        risk_level=risk_level,
        limit=limit,
    )
    return [_approval_payload(r) for r in records]


@router.get("/{approval_id}")
async def get_approval(
    approval_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    record = await container.repository.get_approval(approval_id)
    payload = _approval_payload(record)
    # 详情附带决策影响说明（脱敏）。
    payload["policy_version"] = record.policy_version
    payload["decision"] = record.decision
    payload["edited_action"] = record.edited_action
    payload["supersedes_id"] = record.supersedes_id
    return payload


@router.post("/{approval_id}:decide")
async def decide_approval(
    approval_id: str,
    body: dict[str, Any],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """统一决策入口：approve | edit_and_approve | reject | cancel。

    幂等：相同决策重复调用返回当前运行状态；不同决策返回 409。
    """
    decision = str(body.get("decision") or "")
    if decision not in _DECISIONS:
        raise HTTPException(status_code=422, detail="unsupported decision")
    record = await container.repository.get_approval(approval_id)
    run_id = record.run_id
    edited_action = body.get("edited_action")
    try:
        await container.agent_service.approve(
            run_id,
            approval_id=approval_id,
            decision=decision,
            note=str(body.get("note") or None),
            edited_action=(
                dict(edited_action) if isinstance(edited_action, dict) else None
            ),
            actor=str(body.get("actor") or "operator"),
        )
    except ApplicationError as exc:
        if exc.code == "approval_already_decided":
            raise HTTPException(status_code=409, detail=exc.message) from exc
        raise HTTPException(status_code=400, detail=exc.message) from exc
    updated = await container.repository.get_approval(approval_id)
    return _approval_payload(updated)


@router.get("/stats/summary")
async def approval_stats(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    return await container.repository.get_approval_statistics()


@router.post("/expire-overdue")
async def expire_overdue(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """过期清理：把过期的 pending 审批标记 expired（历史保留）。"""
    count = await container.repository.expire_pending_approvals()
    return {"expired": count}


def _approval_payload(record: Any) -> dict[str, object]:
    request_payload = record.request_payload or {}
    return {
        "id": record.id,
        "run_id": record.run_id,
        "action": record.action,
        "reason": record.reason,
        "status": record.status,
        "approval_type": record.approval_type or "tool_execution",
        "risk_level": record.risk_level or "high",
        "scope": record.scope or "case",
        "requested_action": record.requested_action or record.action,
        "redacted_preview": record.redacted_preview or "",
        "allowed_decisions": record.allowed_decisions or [
            "approve",
            "edit_and_approve",
            "reject",
            "cancel",
        ],
        "expires_at": (
            record.expires_at.isoformat() if record.expires_at else None
        ),
        "decision_payload": record.decision_payload,
        "decided_at": record.decided_at.isoformat() if record.decided_at else None,
        "actor": record.actor,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "request_summary": str(request_payload.get("arguments_summary") or "")[:300],
        "approval_kind": request_payload.get("approval_kind"),
    }
