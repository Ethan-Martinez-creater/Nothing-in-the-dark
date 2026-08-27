"""M16: content-security event views and red-team assessment API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.services.content_security import (
    TRUST_LEVELS,
    ContentEnvelope,
    ContentSecurityService,
    normalize_trust_level,
)

router = APIRouter()


@router.get("/content-security/policy")
async def get_content_security_policy(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """当前内容安全策略版本与运行模式（供前端展示与误报反馈）。"""
    service: ContentSecurityService = container.content_security
    return {
        "mode": service.mode,
        "policy_version": service.policy_version,
        "trust_levels": sorted(TRUST_LEVELS),
        "high_risk_score": 0.7,
        "medium_risk_score": 0.4,
        "detectors": [
            "instruction_override",
            "secret_request",
            "tool_induction",
            "encoding_escape",
        ],
        "hard_boundaries": [
            "外部内容不可伪造 system/developer role",
            "越权工具/秘密读取攻击由策略层阻断（不依赖检测器召回）",
            "未审核外部内容不可写入长期记忆",
            "恶意内容不删除，保留证据供人工审核",
        ],
    }


@router.get("/content-security/assessments")
async def list_content_security_assessments(
    run_id: str | None = Query(default=None),
    trust_level: str | None = Query(default=None),
    disposition: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    records = await container.repository.list_content_security_assessments(
        run_id=run_id,
        trust_level=trust_level,
        disposition=disposition,
        limit=limit,
    )
    return [
        {
            "id": record.id,
            "object_type": record.object_type,
            "object_id": record.object_id,
            "run_id": record.run_id,
            "trust_level": record.trust_level,
            "score": record.score,
            "risk_signals": record.risk_signals,
            "detector": record.detector,
            "disposition": record.disposition,
            "reason": record.reason,
            "content_hash": record.content_hash,
            "review_state": record.review_state,
            "created_at": (
                record.created_at.isoformat() if record.created_at else None
            ),
        }
        for record in records
    ]


@router.get("/content-security/decisions")
async def list_guardrail_decisions(
    run_id: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    records = await container.repository.list_guardrail_decisions(
        run_id=run_id,
        stage=stage,
        decision=decision,
        limit=limit,
    )
    return [
        {
            "id": record.id,
            "stage": record.stage,
            "run_id": record.run_id,
            "turn_id": record.turn_id,
            "tool_call_id": record.tool_call_id,
            "tool": record.tool,
            "decision": record.decision,
            "reason": record.reason,
            "policy_version": record.policy_version,
            "signal_ids": record.signal_ids,
            "content_hash": record.content_hash,
            "summary": record.summary,
            "created_at": (
                record.created_at.isoformat() if record.created_at else None
            ),
        }
        for record in records
    ]


@router.get("/content-security/summary")
async def get_content_security_summary(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    return await container.repository.content_security_summary()


@router.post("/content-security/assess")
async def assess_content(
    body: dict[str, object],
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """对一段文本执行完整评估（调试/红队工具）。

    入参：{"text": ..., "trust_level": "external_content"}。
    返回评估对象与上下文策略处置，原文不回显到 Trace。
    """
    text = str(body.get("text") or "")
    if not text:
        return {"score": 0.0, "signals": [], "disposition": "allowed"}
    trust = normalize_trust_level(str(body.get("trust_level") or ""))
    envelope = ContentEnvelope(
        content=text,
        source_type=str(body.get("source_type") or "manual"),
        source_id=str(body.get("source_id") or "manual-assess"),
        trust=trust,
    )
    service: ContentSecurityService = container.content_security
    context_text, assessment = await service.context_policy(
        envelope,
        object_type=str(body.get("object_type") or "content"),
        object_id=str(body.get("object_id") or "manual-assess"),
    )
    return {
        "score": assessment.score,
        "signals": [s.to_dict() for s in assessment.signals],
        "highest_severity": assessment.highest_severity,
        "disposition": assessment.disposition,
        "reason": assessment.reason,
        "content_hash": assessment.content_hash,
        "context_preview": context_text[:200],
        "policy_version": service.policy_version,
    }
