"""Continuous monitoring & alerting endpoints (01).

监测定义、执行历史、告警规则与告警收件箱。告警状态机：
open -> acknowledged -> resolved；suppressed 可从 open/acknowledged/resolved
进入；非法逆转返回 400。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ApplicationError
from app.schemas.monitoring import (
    AlertResponse,
    AlertStatusRequest,
    ExecutionResponse,
    MonitorCreateRequest,
    MonitorResponse,
    MonitorUpdateRequest,
    RuleCreateRequest,
    RuleResponse,
    RuleUpdateRequest,
    RunNowRequest,
)
from app.services import monitoring
from app.services.alert_state import validate_alert_transition

router = APIRouter()

_VALID_RULE_TYPES = {
    "absolute_volume",
    "rate_growth",
    "anomaly",
    "key_account",
    "narrative",
}


def _validate_schedule(schedule_type: str, interval_seconds: int | None, cron: str | None) -> None:
    if schedule_type == "cron":
        if not cron:
            raise ApplicationError("cron 调度必须提供 cron 表达式", code="monitor_cron_required")
        try:
            monitoring.parse_cron(cron)
        except ValueError as exc:
            raise ApplicationError(f"非法 cron 表达式: {exc}", code="monitor_invalid_cron") from exc
    else:
        if not interval_seconds or interval_seconds <= 0:
            raise ApplicationError(
                "interval 调度必须提供正的 interval_seconds",
                code="monitor_interval_required",
            )


# ---- monitor definitions -------------------------------------------------


@router.post("/{case_id}/monitors", response_model=MonitorResponse, status_code=201)
async def create_monitor(
    case_id: str,
    request: MonitorCreateRequest,
    container: ApplicationContainer = Depends(get_container),
) -> MonitorResponse:
    await container.repository.get_case(case_id)
    _validate_schedule(request.schedule_type, request.interval_seconds, request.cron)
    record = await container.monitor_repository.create_monitor(
        case_id=case_id,
        name=request.name,
        schedule_type=request.schedule_type,
        interval_seconds=request.interval_seconds,
        cron=request.cron,
        timezone=request.timezone,
        query_spec=request.query_spec,
        platforms=request.platforms,
        account_watchlist=request.account_watchlist,
        lookback_seconds=request.lookback_seconds,
        analysis_policy=request.analysis_policy,
    )
    return MonitorResponse.model_validate(record)


@router.get("/{case_id}/monitors", response_model=list[MonitorResponse])
async def list_monitors(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[MonitorResponse]:
    await container.repository.get_case(case_id)
    records = await container.monitor_repository.list_monitors(case_id=case_id)
    return [MonitorResponse.model_validate(r) for r in records]


@router.get("/{case_id}/monitors/{monitor_id}", response_model=MonitorResponse)
async def get_monitor(
    case_id: str,
    monitor_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> MonitorResponse:
    record = await container.monitor_repository.get_monitor(monitor_id)
    if record.case_id != case_id:
        raise ApplicationError("监测不属于该案件", code="monitor_scope_mismatch")
    return MonitorResponse.model_validate(record)


@router.patch("/{case_id}/monitors/{monitor_id}", response_model=MonitorResponse)
async def update_monitor(
    case_id: str,
    monitor_id: str,
    request: MonitorUpdateRequest,
    container: ApplicationContainer = Depends(get_container),
) -> MonitorResponse:
    record = await container.monitor_repository.get_monitor(monitor_id)
    if record.case_id != case_id:
        raise ApplicationError("监测不属于该案件", code="monitor_scope_mismatch")
    schedule_type = request.schedule_type or record.schedule_type
    interval = (
        request.interval_seconds
        if request.interval_seconds is not None
        else record.interval_seconds
    )
    cron = request.cron if request.cron is not None else record.cron
    _validate_schedule(schedule_type, interval, cron)
    updated = await container.monitor_repository.update_monitor(
        monitor_id,
        version=request.version,
        name=request.name,
        schedule_type=request.schedule_type,
        interval_seconds=request.interval_seconds,
        cron=request.cron,
        timezone=request.timezone,
        query_spec=request.query_spec,
        platforms=request.platforms,
        account_watchlist=request.account_watchlist,
        lookback_seconds=request.lookback_seconds,
        analysis_policy=request.analysis_policy,
    )
    return MonitorResponse.model_validate(updated)


@router.delete("/{case_id}/monitors/{monitor_id}", status_code=204)
async def delete_monitor(
    case_id: str,
    monitor_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> None:
    record = await container.monitor_repository.get_monitor(monitor_id)
    if record.case_id != case_id:
        raise ApplicationError("监测不属于该案件", code="monitor_scope_mismatch")
    await container.monitor_repository.delete_monitor(monitor_id)


# ---- monitor actions -----------------------------------------------------


@router.post("/{case_id}/monitors/{monitor_id}:pause", response_model=MonitorResponse)
async def pause_monitor(
    case_id: str,
    monitor_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> MonitorResponse:
    await _ensure_monitor_scope(container, case_id, monitor_id)
    record = await container.monitor_repository.set_monitor_enabled(monitor_id, False)
    return MonitorResponse.model_validate(record)


@router.post("/{case_id}/monitors/{monitor_id}:resume", response_model=MonitorResponse)
async def resume_monitor(
    case_id: str,
    monitor_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> MonitorResponse:
    await _ensure_monitor_scope(container, case_id, monitor_id)
    record = await container.monitor_repository.set_monitor_enabled(monitor_id, True)
    return MonitorResponse.model_validate(record)


@router.post(
    "/{case_id}/monitors/{monitor_id}:run-now",
    response_model=ExecutionResponse,
    status_code=202,
)
async def run_monitor_now(
    case_id: str,
    monitor_id: str,
    request: RunNowRequest,
    container: ApplicationContainer = Depends(get_container),
) -> ExecutionResponse:
    await _ensure_monitor_scope(container, case_id, monitor_id)
    execution = await container.monitor_scheduler.run_now(
        monitor_id,
        idempotency_key=request.idempotency_key,
    )
    return ExecutionResponse.model_validate(execution)


@router.get(
    "/{case_id}/monitors/{monitor_id}/executions",
    response_model=list[ExecutionResponse],
)
async def list_executions(
    case_id: str,
    monitor_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    container: ApplicationContainer = Depends(get_container),
) -> list[ExecutionResponse]:
    await _ensure_monitor_scope(container, case_id, monitor_id)
    records = await container.monitor_repository.list_executions(monitor_id, limit=limit)
    return [ExecutionResponse.model_validate(r) for r in records]


# ---- alert rules ---------------------------------------------------------


@router.post(
    "/{case_id}/monitors/{monitor_id}/rules",
    response_model=RuleResponse,
    status_code=201,
)
async def create_rule(
    case_id: str,
    monitor_id: str,
    request: RuleCreateRequest,
    container: ApplicationContainer = Depends(get_container),
) -> RuleResponse:
    await _ensure_monitor_scope(container, case_id, monitor_id)
    if request.rule_type not in _VALID_RULE_TYPES:
        raise ApplicationError(
            f"未知规则类型 '{request.rule_type}'",
            code="alert_rule_type_unknown",
        )
    record = await container.monitor_repository.create_rule(
        monitor_id=monitor_id,
        rule_type=request.rule_type,
        parameters=request.parameters,
        severity=request.severity,
        cooldown_seconds=request.cooldown_seconds,
        enabled=request.enabled,
    )
    return RuleResponse.model_validate(record)


@router.get("/{case_id}/monitors/{monitor_id}/rules", response_model=list[RuleResponse])
async def list_rules(
    case_id: str,
    monitor_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[RuleResponse]:
    await _ensure_monitor_scope(container, case_id, monitor_id)
    records = await container.monitor_repository.list_rules(monitor_id)
    return [RuleResponse.model_validate(r) for r in records]


@router.patch(
    "/{case_id}/monitors/{monitor_id}/rules/{rule_id}",
    response_model=RuleResponse,
)
async def update_rule(
    case_id: str,
    monitor_id: str,
    rule_id: str,
    request: RuleUpdateRequest,
    container: ApplicationContainer = Depends(get_container),
) -> RuleResponse:
    await _ensure_monitor_scope(container, case_id, monitor_id)
    rule = await container.monitor_repository.get_rule(rule_id)
    if rule.monitor_id != monitor_id:
        raise ApplicationError("规则不属于该监测", code="alert_rule_scope_mismatch")
    updated = await container.monitor_repository.update_rule(
        rule_id,
        version=request.version,
        parameters=request.parameters,
        severity=request.severity,
        cooldown_seconds=request.cooldown_seconds,
        enabled=request.enabled,
    )
    return RuleResponse.model_validate(updated)


@router.delete("/{case_id}/monitors/{monitor_id}/rules/{rule_id}", status_code=204)
async def delete_rule(
    case_id: str,
    monitor_id: str,
    rule_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> None:
    await _ensure_monitor_scope(container, case_id, monitor_id)
    rule = await container.monitor_repository.get_rule(rule_id)
    if rule.monitor_id != monitor_id:
        raise ApplicationError("规则不属于该监测", code="alert_rule_scope_mismatch")
    await container.monitor_repository.delete_rule(rule_id)


# ---- alerts --------------------------------------------------------------


@router.get("/{case_id}/alerts", response_model=list[AlertResponse])
async def list_alerts(
    case_id: str,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    container: ApplicationContainer = Depends(get_container),
) -> list[AlertResponse]:
    await container.repository.get_case(case_id)
    records = await container.monitor_repository.list_alerts(
        case_id=case_id,
        status=status,
        limit=limit,
    )
    return [AlertResponse.model_validate(r) for r in records]


async def _set_alert_status(
    container: ApplicationContainer,
    case_id: str,
    alert_id: str,
    status: str,
    by: str | None,
) -> AlertResponse:
    alert = await container.monitor_repository.get_alert(alert_id)
    monitor = await container.monitor_repository.get_monitor(alert.monitor_id)
    if monitor.case_id != case_id:
        raise ApplicationError("告警不属于该案件", code="alert_scope_mismatch")
    # 共用 alert_state validator（Signal API 与本路由同一状态机）
    validate_alert_transition(alert.status, status)
    updated = await container.monitor_repository.set_alert_status(alert_id, status, by=by)
    return AlertResponse.model_validate(updated)


@router.post("/{case_id}/alerts/{alert_id}:acknowledge", response_model=AlertResponse)
async def acknowledge_alert(
    case_id: str,
    alert_id: str,
    request: AlertStatusRequest,
    container: ApplicationContainer = Depends(get_container),
) -> AlertResponse:
    return await _set_alert_status(container, case_id, alert_id, "acknowledged", request.by)


@router.post("/{case_id}/alerts/{alert_id}:resolve", response_model=AlertResponse)
async def resolve_alert(
    case_id: str,
    alert_id: str,
    request: AlertStatusRequest,
    container: ApplicationContainer = Depends(get_container),
) -> AlertResponse:
    return await _set_alert_status(container, case_id, alert_id, "resolved", request.by)


@router.post("/{case_id}/alerts/{alert_id}:suppress", response_model=AlertResponse)
async def suppress_alert(
    case_id: str,
    alert_id: str,
    request: AlertStatusRequest,
    container: ApplicationContainer = Depends(get_container),
) -> AlertResponse:
    return await _set_alert_status(container, case_id, alert_id, "suppressed", request.by)


async def _ensure_monitor_scope(
    container: ApplicationContainer,
    case_id: str,
    monitor_id: str,
) -> None:
    await container.repository.get_case(case_id)
    record = await container.monitor_repository.get_monitor(monitor_id)
    if record.case_id != case_id:
        raise ApplicationError("监测不属于该案件", code="monitor_scope_mismatch")
