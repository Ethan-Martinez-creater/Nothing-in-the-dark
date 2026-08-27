"""M22: resilience, circuit-breaker, dead-letter and incident API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ApplicationError
from app.services.resilience import (
    CLASSIFICATIONS,
    SCOPE_DATABASE,
    SCOPE_MEDIA,
    SCOPE_MODEL,
    SCOPE_NOTIFICATION,
    SCOPE_PLATFORM,
    SCOPE_TOOL,
)

router = APIRouter()

_SCOPES = frozenset(
    {
        SCOPE_PLATFORM,
        SCOPE_MODEL,
        SCOPE_TOOL,
        SCOPE_MEDIA,
        SCOPE_NOTIFICATION,
        SCOPE_DATABASE,
    }
)


class KillSwitchCreateRequest(BaseModel):
    scope: str = Field(default="global")
    target: str = Field(default="*")
    reason: str = Field(default="", max_length=2000)
    actor: str = Field(default="operator", max_length=100)
    approval_id: str | None = None


class KillSwitchDisableRequest(BaseModel):
    approval_id: str | None = None
    actor: str = Field(default="operator", max_length=100)
    reason: str = Field(default="", max_length=2000)


class DeadLetterActionRequest(BaseModel):
    actor: str = Field(default="operator", max_length=100)
    reason: str = Field(default="", max_length=2000)
    approval_id: str | None = None


class IncidentCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    severity: str = Field(default="warning", pattern="^(info|warning|critical)$")
    impact: str = Field(default="", max_length=4000)
    metrics: dict[str, Any] = Field(default_factory=dict)


class IncidentCloseRequest(BaseModel):
    recovery: dict[str, Any] = Field(default_factory=dict)
    retro: dict[str, Any] = Field(default_factory=dict)


@router.get("/health")
async def health_matrix(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    return await container.resilience.health_summary()


@router.get("/circuits")
async def circuit_states(
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    records = await container.resilience_repository.list_breaker_states()
    return [
        {
            "dependency": record.dependency,
            "scope": record.scope,
            "state": record.state,
            "failure_count": record.failure_count,
            "success_count": record.success_count,
            "config_version": record.config_version,
            "opened_at": (
                record.opened_at.isoformat() if record.opened_at else None
            ),
            "half_open_probe_at": (
                record.half_open_probe_at.isoformat()
                if record.half_open_probe_at
                else None
            ),
            "updated_at": record.updated_at.isoformat()
            if record.updated_at
            else None,
        }
        for record in records
    ]


@router.get("/queues")
async def queue_backpressure(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    admission = container.resilience.admission
    return {
        "admission": {
            "queue_capacity": admission.queue_capacity,
            "max_wait_seconds": admission.max_wait_seconds,
            "db_watermark": admission.db_watermark,
            "disk_watermark": admission.disk_watermark,
            "budget_exhausted": admission.budget_exhausted,
            "reserved_slots": admission.reserved_slots,
        },
        "policy_version": container.settings.resilience_policy_version,
    }


@router.get("/dead-letters")
async def list_dead_letters(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    records = await container.resilience_repository.list_dead_letters(status)
    return [_dead_letter_payload(r) for r in records[:limit]]


@router.post("/dead-letters/{dead_letter_id}:retry")
async def retry_dead_letter(
    dead_letter_id: str,
    body: DeadLetterActionRequest,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """手工重放前重新校验当前策略、数据版本与幂等状态。"""
    record = await container.resilience_repository.get_dead_letter(dead_letter_id)
    if record is None:
        raise HTTPException(status_code=404, detail="dead letter not found")
    if record.code_version != container.settings.app_version:
        raise HTTPException(
            status_code=409,
            detail="policy or code version changed; re-approve before replay",
        )
    authorization = await _require_m21_approval(
        container,
        body.approval_id,
        action_family="dead_letter_retry",
        resource_id=dead_letter_id,
        parameters={"dead_letter_id": dead_letter_id},
    )
    updated = await container.resilience_repository.update_dead_letter(
        dead_letter_id,
        status="retrying",
        recovery_hint="manually replayed by " + body.actor,
        authorization=authorization,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="dead letter not found")
    return _dead_letter_payload(updated)


@router.post("/dead-letters/{dead_letter_id}:resolve")
async def resolve_dead_letter(
    dead_letter_id: str,
    body: DeadLetterActionRequest,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    updated = await container.resilience_repository.update_dead_letter(
        dead_letter_id,
        status="resolved",
        recovery_hint="resolved by " + body.actor + ": " + body.reason,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="dead letter not found")
    return _dead_letter_payload(updated)


@router.get("/kill-switches")
async def list_kill_switches(
    active_only: bool = Query(default=False),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    records = await container.resilience_repository.list_kill_switches(
        active_only=active_only
    )
    return [_kill_switch_payload(r) for r in records]


@router.post("/kill-switches")
async def enable_kill_switch(
    body: KillSwitchCreateRequest,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """开启 Kill Switch：停止新任务并按策略处置在途（操作可审计）。"""
    if body.scope not in _SCOPES and body.scope != "global":
        raise HTTPException(status_code=422, detail="unsupported kill switch scope")
    authorization = await _require_m21_approval(
        container,
        body.approval_id,
        action_family="kill_switch",
        resource_id=f"enable:{body.scope}:{body.target}",
        parameters={
            "scope": body.scope,
            "target": body.target,
        },
    )
    record = await container.resilience_repository.create_kill_switch(
        scope=body.scope,
        target=body.target,
        reason=body.reason,
        actor=body.actor,
        approval_id=body.approval_id,
        authorization=authorization,
    )
    if container.resilience._telemetry is not None:
        try:
            container.resilience._telemetry.metrics.increment(
                "resilience.kill_switch_on"
            )
        except Exception:  # noqa: BLE001
            pass
    return _kill_switch_payload(record)


@router.post("/kill-switches/{kill_switch_id}:disable")
async def disable_kill_switch(
    kill_switch_id: str,
    body: KillSwitchDisableRequest,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    authorization = await _require_m21_approval(
        container,
        body.approval_id,
        action_family="kill_switch",
        resource_id=f"disable:{kill_switch_id}",
        parameters={"kill_switch_id": kill_switch_id},
    )
    record = await container.resilience_repository.disable_kill_switch(
        kill_switch_id,
        actor=body.actor,
        reason=body.reason,
        authorization=authorization,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="kill switch not found")
    return _kill_switch_payload(record)


@router.get("/incidents")
async def list_incidents(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    records = await container.resilience_repository.list_incidents(status)
    return [_incident_payload(r) for r in records[:limit]]


@router.post("/incidents")
async def create_incident(
    body: IncidentCreateRequest,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    record = await container.resilience.open_incident(
        title=body.title,
        severity=body.severity,
        impact=body.impact,
        metrics=body.metrics,
    )
    return _incident_payload(record)


@router.get("/incidents/{incident_id}")
async def get_incident(
    incident_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    record = await container.resilience_repository.get_incident(incident_id)
    if record is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return _incident_payload(record)


@router.post("/incidents/{incident_id}:close")
async def close_incident(
    incident_id: str,
    body: IncidentCloseRequest,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    record = await container.resilience_repository.close_incident(
        incident_id, recovery=body.recovery, retro=body.retro
    )
    if record is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return _incident_payload(record)


@router.get("/classifications")
async def list_classifications() -> list[dict[str, object]]:
    from app.services.resilience import RETRYABLE

    return [
        {"classification": value, "retryable": RETRYABLE[value]}
        for value in sorted(CLASSIFICATIONS)
    ]


async def _require_m21_approval(
    container: ApplicationContainer,
    approval_id: str | None,
    *,
    action_family: str,
    resource_id: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """签发一次性执行授权（M21/M22 防重放）。

    校验真实、未过期的 approved 决策；签发绑定 action_family + 资源 +
    参数哈希的授权记录（approval_id 唯一）。返回的 authorization 字典
    由业务仓储在同一事务中原子消费——同一审批第二次用于任何操作都会被
    唯一约束或消费判定拒绝（409）。
    """
    if not approval_id:
        raise HTTPException(
            status_code=409,
            detail=f"{action_family} operation requires M21 approval",
        )
    try:
        approval = await container.repository.get_approval(approval_id)
    except Exception as exc:
        raise HTTPException(
            status_code=409, detail="approval_id is unknown"
        ) from exc
    if approval.status not in {"approved", "approved_with_edits"}:
        raise HTTPException(
            status_code=409, detail="approval has not been approved"
        )
    expires_at = approval.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at is not None and expires_at < datetime.now(UTC):
        raise HTTPException(status_code=409, detail="approval has expired")
    declared_action = " ".join(
        str(value or "")
        for value in (
            approval.approval_type,
            approval.action,
            approval.requested_action,
        )
    ).lower()
    if (
        approval.approval_type != "policy_exception"
        and action_family not in declared_action
    ):
        raise HTTPException(
            status_code=409,
            detail="approval is not scoped to this operation",
        )
    try:
        from app.application.authorization_service import argument_hash

        await container.authorization.issue(
            approval_id,
            action_family=action_family,
            resource_id=resource_id,
            parameters=parameters,
            run_id=approval.run_id,
        )
    except ApplicationError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return {
        "approval_id": approval_id,
        "action_family": action_family,
        "resource_id": resource_id,
        "argument_hash": argument_hash(parameters),
    }

def _dead_letter_payload(record: Any) -> dict[str, object]:
    return {
        "id": record.id,
        "operation_key": record.operation_key,
        "dependency": record.dependency,
        "scope": record.scope,
        "error_classification": record.error_classification,
        "error_code": record.error_code,
        "attempts": record.attempts,
        "payload_hash": record.payload_hash,
        "policy_version": record.policy_version,
        "code_version": record.code_version,
        "recovery_hint": record.recovery_hint,
        "payload_ref": record.payload_ref,
        "status": record.status,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "resolved_at": record.resolved_at.isoformat() if record.resolved_at else None,
    }


def _kill_switch_payload(record: Any) -> dict[str, object]:
    return {
        "id": record.id,
        "scope": record.scope,
        "target": record.target,
        "status": record.status,
        "reason": record.reason,
        "actor": record.actor,
        "approval_id": record.approval_id,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "disabled_at": record.disabled_at.isoformat() if record.disabled_at else None,
    }


def _incident_payload(record: Any) -> dict[str, object]:
    return {
        "id": record.id,
        "title": record.title,
        "severity": record.severity,
        "status": record.status,
        "impact": record.impact,
        "timeline": record.timeline_json,
        "metrics": record.metrics_json,
        "actions": record.actions_json,
        "recovery": record.recovery_json,
        "retro": record.retro_json,
        "kill_switch_ids": record.kill_switch_ids,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "closed_at": record.closed_at.isoformat() if record.closed_at else None,
    }
