"""13 调查结果订阅与外部协作 API。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ApplicationError

router = APIRouter()


class SubscriptionCreate(BaseModel):
    name: str = ""
    event_filters: list[str] = []
    severity: str = "info"
    channel: str = "inbox"
    endpoint_id: str | None = None
    schedule: str = "instant"
    quiet_hours: dict[str, object] | None = None


class EndpointCreate(BaseModel):
    name: str = ""
    url: str = Field(min_length=1)
    secret_ref: str = ""
    allowed_event_types: list[str] = []


class TestEventCreate(BaseModel):
    event_type: str = "test.event"
    severity: str = "info"
    data: dict[str, object] = {}


class ShareLinkCreate(BaseModel):
    target_type: str = "artifact"
    target_id: str = ""
    expires_in_hours: int | None = None
    download_limit: int = 0


class ExportCreate(BaseModel):
    scope: str = "case"
    scope_ref: str = ""
    format: str = "json"
    redaction_policy: str = "standard"


@router.post("/{case_id}/subscriptions", status_code=201)
async def create_subscription(
    case_id: str,
    request: SubscriptionCreate,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    record = await container.notification_service.create_subscription(
        case_id=case_id,
        name=request.name,
        event_filters=request.event_filters,
        severity=request.severity,
        channel=request.channel,
        endpoint_id=request.endpoint_id,
        schedule=request.schedule,
        quiet_hours=request.quiet_hours,
    )
    return _subscription_summary(record)


@router.get("/{case_id}/subscriptions")
async def list_subscriptions(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    records = await container.notification_service.list_subscriptions(case_id)
    return {"subscriptions": [_subscription_summary(r) for r in records]}


@router.post("/{case_id}/subscriptions/{subscription_id}:pause")
async def pause_subscription(
    case_id: str,
    subscription_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    record = await container.notification_service.set_subscription_enabled(
        case_id, subscription_id, False
    )
    return _subscription_summary(record)


@router.post("/{case_id}/subscriptions/{subscription_id}:resume")
async def resume_subscription(
    case_id: str,
    subscription_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    record = await container.notification_service.set_subscription_enabled(
        case_id, subscription_id, True
    )
    return _subscription_summary(record)


@router.post("/{case_id}/notification-endpoints", status_code=201)
async def create_endpoint(
    case_id: str,
    request: EndpointCreate,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    try:
        endpoint = await container.notification_service.create_endpoint(
            case_id=case_id,
            name=request.name,
            url=request.url,
            secret_ref=request.secret_ref,
            allowed_event_types=request.allowed_event_types,
        )
    except ApplicationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    return {
        "id": endpoint.id,
        "name": endpoint.name,
        "url": endpoint.url,
        "verification_state": endpoint.verification_state,
    }


@router.post("/{case_id}/notification-endpoints/{endpoint_id}:verify")
async def verify_endpoint(
    case_id: str,
    endpoint_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    try:
        endpoint = await container.notification_service.verify_endpoint(
            case_id=case_id, endpoint_id=endpoint_id
        )
    except ApplicationError as exc:
        status_code = 404 if exc.code == "notification_endpoint_not_found" else 422
        raise HTTPException(status_code=status_code, detail=exc.message) from exc
    return {
        "id": endpoint.id,
        "verification_state": endpoint.verification_state,
    }


@router.get("/{case_id}/notification-endpoints")
async def list_endpoints(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    records = await container.notification_service.list_endpoints(case_id)
    return {
        "endpoints": [
            {
                "id": r.id,
                "name": r.name,
                "url": r.url,
                "verification_state": r.verification_state,
                "enabled": r.enabled,
            }
            for r in records
        ]
    }


@router.post("/{case_id}/notification-events", status_code=201)
async def enqueue_test_event(
    case_id: str,
    request: TestEventCreate,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """入队一条测试事件（Outbox），Dispatcher 将按订阅投递。"""
    event = await container.notification_service.enqueue_event(
        event_id=f"test-{datetime.now(UTC).timestamp():.0f}",
        event_type=request.event_type,
        case_id=case_id,
        severity=request.severity,
        data=request.data,
    )
    return {"event_id": event.event_id, "status": "queued"}


@router.get("/{case_id}/notifications")
async def list_notifications(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """站内通知中心：本案件已入队的领域事件。"""
    records = await container.repository.list_notification_events(case_id, limit=100)
    return {
        "events": [
            {
                "id": r.id,
                "event_id": r.event_id,
                "event_type": r.event_type,
                "severity": r.severity,
                "classification": r.classification,
                "data": r.data,
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            }
            for r in records
        ]
    }


@router.get("/{case_id}/deliveries")
async def list_deliveries(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    records = await container.repository.list_deliveries(case_id, limit=100)
    return {
        "deliveries": [
            {
                "id": r.id,
                "event_id": r.event_id,
                "subscription_id": r.subscription_id,
                "endpoint_id": r.endpoint_id,
                "attempt": r.attempt,
                "status": r.status,
                "http_status": r.http_status,
                "http_summary": r.http_summary,
                "next_retry_at": (
                    r.next_retry_at.isoformat() if r.next_retry_at else None
                ),
                "error_code": r.error_code,
            }
            for r in records
        ]
    }


@router.post("/{case_id}/deliveries/{delivery_id}:retry")
async def retry_delivery(
    case_id: str,
    delivery_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """手工重投死信：保持 event_id 幂等。"""
    reset = await container.repository.reset_delivery_for_retry(case_id, delivery_id)
    if not reset:
        raise HTTPException(status_code=404, detail="delivery not found")
    return {"delivery_id": delivery_id, "status": "pending"}


@router.post("/{case_id}/share-links", status_code=201)
async def create_share_link(
    case_id: str,
    request: ShareLinkCreate,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    expires_at = None
    if request.expires_in_hours:
        expires_at = datetime.now(UTC) + timedelta(hours=request.expires_in_hours)
    result = await container.notification_service.create_share_link(
        case_id=case_id,
        target_type=request.target_type,
        target_id=request.target_id,
        expires_at=expires_at,
        download_limit=request.download_limit,
    )
    return {
        "token": result["token"],
        "link_id": result["link_id"],
        "expires_at": expires_at.isoformat() if expires_at else None,
        "note": "只读分享在无身份系统下仅用于本地可信部署，并默认关闭外部渠道。",
    }


@router.get("/share-links/{token}")
async def resolve_share_link(
    token: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """外部只读访问：过期/撤销/超限返回 404/410。"""
    try:
        resolved = await container.notification_service.resolve_share_link(token)
    except ApplicationError as exc:
        if exc.code == "share_link_rate_limited":
            status = 429
        else:
            status = 410 if exc.code in {"share_link_revoked", "share_link_expired"} else 404
        raise HTTPException(status_code=status, detail=exc.message) from exc
    return resolved


@router.post("/{case_id}/export-jobs", status_code=201)
async def create_export_job(
    case_id: str,
    request: ExportCreate,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    job = await container.notification_service.create_export_job(
        case_id=case_id,
        scope=request.scope,
        scope_ref=request.scope_ref,
        format=request.format,
        redaction_policy=request.redaction_policy,
    )
    return {"id": job.id, "status": job.status, "scope": job.scope, "format": job.format}


@router.get("/{case_id}/export-jobs")
async def list_export_jobs(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    records = await container.notification_service.list_export_jobs(case_id)
    return {
        "jobs": [
            {
                "id": r.id,
                "scope": r.scope,
                "scope_ref": r.scope_ref,
                "format": r.format,
                "redaction_policy": r.redaction_policy,
                "status": r.status,
                "artifact_id": r.artifact_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]
    }


def _subscription_summary(record: object) -> dict[str, object]:
    return {
        "id": record.id,
        "case_id": record.case_id,
        "name": record.name,
        "event_filters": record.event_filters,
        "severity": record.severity,
        "channel": record.channel,
        "endpoint_id": record.endpoint_id,
        "schedule": record.schedule,
        "quiet_hours": record.quiet_hours,
        "enabled": record.enabled,
        "version": record.version,
    }
