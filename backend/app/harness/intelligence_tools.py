"""V3 Part G: 5 个只读 Intelligence Tool（§69-§72）。

get_investigation_quality / query_related_investigations /
query_workspace_entities / get_workspace_entity / query_signals

统一约束（§69）：
- permissions=("read_database",)，side_effect="none"，idempotent=True
- requires_approval=False，cache_ttl_seconds=0，execution_class="trusted_in_process"
- Pydantic Input/Output Model + routing-oriented ToolSpec.description
- case_id 由 Runtime 注入（_CASE_SCOPED_TOOLS），模型不得自由构造（§70）
- candidate relation / risk assessment / advanced signal 都是 intelligence
  indicator，不是 verified fact（§72）
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.v3 import (
    MAX_ENTITY_RECENT_POSTS,
    MAX_RELATED_INVESTIGATIONS,
)
from app.harness.tools import ToolRegistry, ToolSpec

_INTEL_TOOL_CONFIG: dict[str, Any] = {
    "version": "1.0.0",
    "permissions": ("read_database",),
    "side_effect": "none",
    "idempotent": True,
    "requires_approval": False,
    "execution_mode": "parallel",
    "cache_ttl_seconds": 0,
    "max_concurrency": 8,
    "timeout_seconds": 10,
    "max_retries": 0,
    "execution_class": "trusted_in_process",
    "filesystem": {},
    "network": {},
    "secrets": (),
    "risk_level": "low",
}

_UNAVAILABLE = {
    "ok": False,
    "error": {
        "code": "intelligence_query_unavailable",
        "message": "Intelligence query service is not configured.",
    },
}


class IntelligenceToolReadService:
    """只读适配层：把 V3 服务的能力暴露给 Tool handler（无 LLM、无副作用）。"""

    def __init__(
        self,
        *,
        quality_service: Any,
        cross_service: Any,
        workspace_service: Any,
        signal_service: Any,
        workspace_repository: Any,
        cross_repository: Any,
    ) -> None:
        self._quality = quality_service
        self._cross = cross_service
        self._workspace = workspace_service
        self._signals = signal_service
        self._workspace_repo = workspace_repository
        self._cross_repo = cross_repository

    # ---------------- get_investigation_quality ----------------

    async def get_investigation_quality(self, case_id: str) -> dict[str, Any]:
        payload = await self._quality.evaluate(case_id)
        return {
            "case_id": payload.get("case_id", case_id),
            "overall_score": payload.get("overall_score"),
            "grade": payload.get("grade"),
            "dimensions": payload.get("dimensions", []),
            "top_gaps": (payload.get("gaps") or [])[:5],
            "computed_at": payload.get("computed_at"),
            "algorithm_version": payload.get("algorithm_version"),
        }

    # ---------------- query_related_investigations ----------------

    async def query_related_investigations(
        self,
        *,
        case_id: str,
        relation_type: str | None = None,
        status: str | None = None,
        min_score: float | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        related = await self._cross.related_investigations(
            case_id, limit=MAX_RELATED_INVESTIGATIONS
        )
        if relation_type:
            related = [
                item for item in related if relation_type in item["relation_types"]
            ]
        if status:
            related = [
                item
                for item in related
                if (item.get("has_candidate_relation") and status == "candidate")
                or (not item.get("has_candidate_relation") and status == "observed")
            ]
        if min_score is not None:
            related = [
                item for item in related if item["max_score"] >= min_score
            ]
        return {
            "related_investigations": [
                {
                    "case_id": item["case_id"],
                    "title": item.get("title"),
                    "relation_types": item["relation_types"],
                    "relation_count": item["relation_count"],
                    "max_score": item["max_score"],
                    "evidence_counts": {
                        "shared_actor": item.get("shared_actor_count", 0),
                        "shared_post": item.get("shared_post_count", 0),
                        "shared_media": item.get("shared_media_count", 0),
                        "shared_content": item.get("shared_content_count", 0),
                    },
                    "has_candidate_relation": item.get("has_candidate_relation", False),
                }
                for item in related[:limit]
            ],
            "total": len(related),
        }

    # ---------------- query_workspace_entities ----------------

    async def query_workspace_entities(
        self,
        *,
        case_id: str,
        query: str | None = None,
        platform: str | None = None,
        min_investigations: int = 0,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        # §71：默认只返回当前 Case 直接出现的 Entity / Identity Component，
        # 不允许默认全 Workspace dump。
        payload = await self._workspace.list_case_entities(
            case_id,
            query=query,
            platform=platform,
            limit=limit,
            offset=offset,
        )
        if min_investigations > 0:
            items = [
                item
                for item in payload.get("items", [])
                if item.get("investigation_count", 0) >= min_investigations
            ]
            return {"items": items[:limit], "total": len(items)}
        return payload

    # ---------------- get_workspace_entity ----------------

    async def get_workspace_entity(
        self, *, case_id: str, entity_id: str
    ) -> dict[str, Any]:
        """§71 scope：当前 Case 有 CaseLink OR identity component 与当前
        Case 存在 active related Investigation 关系；否则 found=false。"""
        case_links = await self._workspace_repo.list_case_links(
            entity_id, case_id=case_id
        )
        if not case_links:
            try:
                profile = await self._workspace.get_profile(entity_id)
            except Exception:
                return {"found": False}
            entity_cases = set(profile.get("investigations") or [])
            related = set(await self._cross_repo.related_case_ids(case_id))
            if not (entity_cases & related):
                return {"found": False}
        else:
            profile = await self._workspace.get_profile(entity_id)
        return {
            "found": True,
            "component_key": profile.get("component_key"),
            "entity_ids": profile.get("entity_ids", []),
            "canonical_name": profile.get("canonical_name"),
            "platform_identities": profile.get("platform_identities", []),
            "case_appearances": profile.get("investigations", []),
            "recent_posts": (profile.get("recent_posts") or [])[:MAX_ENTITY_RECENT_POSTS],
            "risk_summary": profile.get("risk_summary"),
            "risk_assessments": profile.get("risk_assessments", []),
            "unresolved_local_risk": profile.get("unresolved_local_risk", []),
            "coordination_memberships": profile.get("coordination_memberships", []),
            "algorithm_version": profile.get("algorithm_version"),
        }

    # ---------------- query_signals ----------------

    async def query_signals(
        self,
        *,
        case_id: str,
        status: str | None = None,
        severity: str | None = None,
        signal_type: str | None = None,
        source_type: str | None = None,
        detector_active: bool | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        # §71：Derived source 必须通过 derived_signal_case_links 做 Case Scope
        # （SignalService.list_signals(case_id=...) 已 JOIN）。
        signals = await self._signals.list_signals(
            statuses=status.split(",") if status else None,
            severity=severity,
            case_id=case_id,
            signal_type=signal_type,
            source_type=source_type,
            detector_active=detector_active,
            limit=limit,
        )
        return {
            "signals": [
                {
                    "id": signal.id,
                    "source_type": signal.source_type,
                    "source_label": signal.source_label,
                    "signal_type": signal.signal_type,
                    "severity": signal.severity,
                    "status": signal.status,
                    "title": signal.title,
                    "why_it_matters": signal.why_it_matters,
                    "confidence": signal.confidence,
                    "detector_active": signal.detector_active,
                    "detector_version": signal.detector_version,
                    "related_case_ids": signal.related_case_ids,
                    "detected_at": (
                        signal.detected_at.isoformat() if signal.detected_at else None
                    ),
                }
                for signal in signals
            ],
            "total": len(signals),
        }


# ---------------------------------------------------------------------------
# Input / Output Models（§71）
# ---------------------------------------------------------------------------


class CaseScopedInput(BaseModel):
    case_id: str | None = Field(
        default=None,
        description="Injected by runtime; never model-controlled.",
    )


class GetInvestigationQualityInput(CaseScopedInput):
    pass


class GetInvestigationQualityOutput(BaseModel):
    case_id: str
    overall_score: float | None = None
    grade: str
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    top_gaps: list[dict[str, Any]] = Field(default_factory=list)
    computed_at: str | None = None
    algorithm_version: str | None = None


class QueryRelatedInvestigationsInput(CaseScopedInput):
    relation_type: str | None = Field(
        default=None,
        description=(
            "Optional relation type filter: shared_actor, shared_post, "
            "shared_media, or shared_content."
        ),
    )
    status: Literal["observed", "candidate"] | None = Field(
        default=None,
        description="Optional status filter; candidate means not yet confirmed.",
    )
    min_score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Minimum max_score (0..1)."
    )
    limit: int = Field(default=10, ge=1, le=50)


class RelatedInvestigationItem(BaseModel):
    case_id: str
    title: str | None = None
    relation_types: list[str] = Field(default_factory=list)
    relation_count: int = 0
    max_score: float = 0.0
    evidence_counts: dict[str, int] = Field(default_factory=dict)
    has_candidate_relation: bool = False


class QueryRelatedInvestigationsOutput(BaseModel):
    related_investigations: list[RelatedInvestigationItem] = Field(
        default_factory=list
    )
    total: int = 0


class QueryWorkspaceEntitiesInput(CaseScopedInput):
    query: str | None = Field(default=None, max_length=300)
    platform: str | None = Field(default=None, max_length=32)
    min_investigations: int = Field(default=0, ge=0, le=1000)
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=5000)


class WorkspaceEntityItem(BaseModel):
    entity_id: str
    canonical_name: str
    platforms: list[str] = Field(default_factory=list)
    investigation_count: int = 0
    post_count: int = 0
    comment_count: int = 0
    risk_summary: str | None = None


class QueryWorkspaceEntitiesOutput(BaseModel):
    items: list[WorkspaceEntityItem] = Field(default_factory=list)
    total: int = 0


class GetWorkspaceEntityInput(CaseScopedInput):
    entity_id: str = Field(description="Stable Workspace Entity id (entity_id).")


class GetWorkspaceEntityOutput(BaseModel):
    found: bool = True
    component_key: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    canonical_name: str | None = None
    platform_identities: list[dict[str, str]] = Field(default_factory=list)
    case_appearances: list[str] = Field(default_factory=list)
    recent_posts: list[dict[str, Any]] = Field(default_factory=list)
    risk_summary: str | None = None
    risk_assessments: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_local_risk: list[dict[str, Any]] = Field(default_factory=list)
    coordination_memberships: list[dict[str, Any]] = Field(default_factory=list)
    algorithm_version: str | None = None


class QuerySignalsInput(CaseScopedInput):
    status: str | None = Field(
        default=None,
        description="Comma-separated statuses: open, acknowledged, resolved, suppressed.",
    )
    severity: str | None = Field(default=None)
    signal_type: str | None = Field(default=None)
    source_type: str | None = Field(default=None)
    detector_active: bool | None = None
    limit: int = Field(default=20, ge=1, le=50)


class SignalItem(BaseModel):
    id: str
    source_type: str
    source_label: str
    signal_type: str
    severity: str
    status: str
    title: str
    why_it_matters: str
    confidence: float | None = None
    detector_active: bool | None = None
    detector_version: str | None = None
    related_case_ids: list[str] = Field(default_factory=list)
    detected_at: str | None = None


class QuerySignalsOutput(BaseModel):
    signals: list[SignalItem] = Field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
# Registration（§69）
# ---------------------------------------------------------------------------


def register_intelligence_tools(
    registry: ToolRegistry,
    service: IntelligenceToolReadService | None,
) -> None:
    """Register V3 intelligence tools (idempotent; duplicate names are skipped)."""

    async def get_investigation_quality(arguments: BaseModel) -> dict[str, Any]:
        request = GetInvestigationQualityInput.model_validate(arguments)
        if service is None or not request.case_id:
            return dict(_UNAVAILABLE)
        return await service.get_investigation_quality(request.case_id)

    async def query_related_investigations(arguments: BaseModel) -> dict[str, Any]:
        request = QueryRelatedInvestigationsInput.model_validate(arguments)
        if service is None or not request.case_id:
            return dict(_UNAVAILABLE)
        return await service.query_related_investigations(
            case_id=request.case_id,
            relation_type=request.relation_type,
            status=request.status,
            min_score=request.min_score,
            limit=request.limit,
        )

    async def query_workspace_entities(arguments: BaseModel) -> dict[str, Any]:
        request = QueryWorkspaceEntitiesInput.model_validate(arguments)
        if service is None or not request.case_id:
            return dict(_UNAVAILABLE)
        return await service.query_workspace_entities(
            case_id=request.case_id,
            query=request.query,
            platform=request.platform,
            min_investigations=request.min_investigations,
            limit=request.limit,
            offset=request.offset,
        )

    async def get_workspace_entity(arguments: BaseModel) -> dict[str, Any]:
        request = GetWorkspaceEntityInput.model_validate(arguments)
        if service is None or not request.case_id:
            return dict(_UNAVAILABLE)
        return await service.get_workspace_entity(
            case_id=request.case_id, entity_id=request.entity_id
        )

    async def query_signals(arguments: BaseModel) -> dict[str, Any]:
        request = QuerySignalsInput.model_validate(arguments)
        if service is None or not request.case_id:
            return dict(_UNAVAILABLE)
        return await service.query_signals(
            case_id=request.case_id,
            status=request.status,
            severity=request.severity,
            signal_type=request.signal_type,
            source_type=request.source_type,
            detector_active=request.detector_active,
            limit=request.limit,
        )

    _REGISTRATIONS: list[ToolSpec] = [
        ToolSpec(
            name="get_investigation_quality",
            description=(
                "Investigation Quality question: \"what is missing in the "
                "current investigation\", \"how complete is it\", \"is it "
                "ready for a report\" → use this tool. Returns overall score, "
                "grade, six dimension scores, and top gaps. IMPORTANT: Quality "
                "measures investigation completeness/readiness, NOT truth "
                "score; it does not validate whether facts are true."
            ),
            input_model=GetInvestigationQualityInput,
            handler=get_investigation_quality,
            output_model=GetInvestigationQualityOutput,
            **_INTEL_TOOL_CONFIG,
        ),
        ToolSpec(
            name="query_related_investigations",
            description=(
                "Cross-case relation question: \"how is this event related to "
                "past investigations\", \"are there duplicate accounts, media, "
                "or posts across cases\" → use this tool. Lists related "
                "investigations with relation types and observed/candidate "
                "status. A candidate relation is an intelligence indicator, "
                "not a verified fact."
            ),
            input_model=QueryRelatedInvestigationsInput,
            handler=query_related_investigations,
            output_model=QueryRelatedInvestigationsOutput,
            **_INTEL_TOOL_CONFIG,
        ),
        ToolSpec(
            name="query_workspace_entities",
            description=(
                "Actor/account question: \"has this account appeared in other "
                "events\", \"which platform identities does this actor have\" "
                "→ use this tool. Lists entities/identity components that "
                "directly appear in the current case (case-scoped, never a "
                "workspace-wide dump)."
            ),
            input_model=QueryWorkspaceEntitiesInput,
            handler=query_workspace_entities,
            output_model=QueryWorkspaceEntitiesOutput,
            **_INTEL_TOOL_CONFIG,
        ),
        ToolSpec(
            name="get_workspace_entity",
            description=(
                "Get the full profile of one workspace entity by entity_id: "
                "identity component, platform identities, case appearances, "
                "recent posts, risk summary, and coordination memberships. "
                "Only entities linked to the current case or connected via an "
                "active related investigation are visible; otherwise found=false."
            ),
            input_model=GetWorkspaceEntityInput,
            handler=get_workspace_entity,
            output_model=GetWorkspaceEntityOutput,
            **_INTEL_TOOL_CONFIG,
        ),
        ToolSpec(
            name="query_signals",
            description=(
                "Advanced anomaly/signal question: \"what anomalies exist now\", "
                "\"is there coordination behavior\", \"repeated actors or media\", "
                "\"which cases overlap heavily\" → use this tool. Lists signals "
                "relevant to the current case (monitor alerts + derived "
                "signals). A risk signal indicates possible risk; it is not "
                "proof that an actor is malicious."
            ),
            input_model=QuerySignalsInput,
            handler=query_signals,
            output_model=QuerySignalsOutput,
            **_INTEL_TOOL_CONFIG,
        ),
    ]
    for spec in _REGISTRATIONS:
        registry.register(spec)
