"""Global domain-scoped memory management.

Domain memories (``scope="domain"``, no case binding) hold long-lived working
knowledge shared across cases. The repository scopes every case-facing query
by ``case_id``, so domain memories never leak into case memory listing, RAG
search or the per-case context builder — isolation by construction.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ApplicationError
from app.schemas.knowledge import CreateMemoryRequest, MemoryResponse

router = APIRouter()


@router.get("/domain", response_model=list[MemoryResponse])
async def list_domain_memories(
    include_inactive: bool = Query(default=False),
    container: ApplicationContainer = Depends(get_container),
) -> list[MemoryResponse]:
    records = await container.knowledge.list_memories(
        None,
        include_inactive=include_inactive,
        scope="domain",
    )
    return [MemoryResponse.model_validate(record) for record in records]


@router.post(
    "/domain",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_domain_memory(
    request: CreateMemoryRequest,
    container: ApplicationContainer = Depends(get_container),
) -> MemoryResponse:
    """Create a domain-scoped memory; ``scope`` is forced to ``domain``."""
    vectors = await container.embeddings.embed([request.content])
    governed_request = CreateMemoryRequest(
        **request.model_dump(exclude={"scope"}),
        scope="domain",
    )
    record = await container.memory_governance.persist_governed(
        case_id=None,
        request=governed_request,
        trust_level="operator_input",
        explicit_user_input=True,
        has_evidence=False,
        embedding=vectors[0] if vectors else None,
        actor="operator",
    )
    return MemoryResponse.model_validate(record)

# ---------- M23: 记忆安全与用户可控治理 ----------


class MemoryFilterRequest(BaseModel):
    scope: str | None = None
    memory_type: str | None = None
    status: str | None = None
    source_type: str | None = None
    include_inactive: bool = False
    limit: int = Field(default=100, ge=1, le=500)


class MemoryActionRequest(BaseModel):
    actor: str = Field(default="operator", max_length=100)
    reason: str = Field(default="", max_length=2000)


class MemoryCorrectRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    actor: str = Field(default="operator", max_length=100)
    reason: str = Field(default="", max_length=2000)
    importance: float = Field(default=0.7, ge=0, le=1)
    confidence: float = Field(default=1, ge=0, le=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class MemoryReviewRequest(BaseModel):
    accept: bool
    actor: str = Field(default="operator", max_length=100)
    reason: str = Field(default="", max_length=2000)


class ReindexRequest(BaseModel):
    scope: str | None = None
    status: str | None = None
    memory_type: str | None = None
    dry_run: bool = False
    embedding_version: str = "1.0"
    limit: int = Field(default=500, ge=1, le=2000)


@router.get("")
async def list_governed_memories(
    scope: str | None = Query(default=None),
    memory_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    container: ApplicationContainer = Depends(get_container),
) -> list[MemoryResponse]:
    """M23: 记忆治理列表（scope/type/status/source/expiry 筛选）。"""
    records = await container.knowledge.list_memories(
        None,
        include_inactive=include_inactive,
        scope=scope,
        memory_type=memory_type,
        status=status,
        source_type=source_type,
        limit=limit,
    )
    return [MemoryResponse.model_validate(r) for r in records]


@router.get("/{memory_id}")
async def get_memory_detail(
    memory_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> MemoryResponse:
    record = await container.knowledge.get_memory(memory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return MemoryResponse.model_validate(record)


@router.post("/{memory_id}:correct", response_model=MemoryResponse)
async def correct_memory(
    memory_id: str,
    body: MemoryCorrectRequest,
    container: ApplicationContainer = Depends(get_container),
) -> MemoryResponse:
    """修正产生新版本；旧版本 superseded 不再检索（历史可审计）。"""
    request = CreateMemoryRequest(
        scope="case",
        kind="fact",
        content=body.content,
        source_type="operator_correction",
        source_id=memory_id,
        importance=body.importance,
        confidence=body.confidence,
        metadata=body.metadata,
    )
    vectors = await container.embeddings.embed([body.content])
    try:
        record = await container.memory_governance.correct_memory(
            memory_id,
            request,
            actor=body.actor,
            reason=body.reason,
            embedding=vectors[0] if vectors else None,
        )
    except ApplicationError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return MemoryResponse.model_validate(record)


@router.post("/{memory_id}:disable", response_model=MemoryResponse)
async def disable_memory(
    memory_id: str,
    body: MemoryActionRequest,
    container: ApplicationContainer = Depends(get_container),
) -> MemoryResponse:
    try:
        record = await container.memory_governance.disable_memory(
            memory_id, actor=body.actor, reason=body.reason
        )
    except ApplicationError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return MemoryResponse.model_validate(record)


@router.post("/{memory_id}:restore", response_model=MemoryResponse)
async def restore_memory(
    memory_id: str,
    body: MemoryActionRequest,
    container: ApplicationContainer = Depends(get_container),
) -> MemoryResponse:
    try:
        record = await container.memory_governance.restore_memory(
            memory_id, actor=body.actor, reason=body.reason
        )
    except ApplicationError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return MemoryResponse.model_validate(record)


@router.post("/{memory_id}:delete", response_model=MemoryResponse)
async def delete_memory(
    memory_id: str,
    body: MemoryActionRequest,
    container: ApplicationContainer = Depends(get_container),
) -> MemoryResponse:
    """逻辑删除：状态 deleted，从检索索引立即移除；重复删除幂等。"""
    try:
        record = await container.memory_governance.delete_memory(
            memory_id, actor=body.actor, reason=body.reason
        )
    except ApplicationError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return MemoryResponse.model_validate(record)


@router.post("/{memory_id}:review", response_model=MemoryResponse)
async def review_memory(
    memory_id: str,
    body: MemoryReviewRequest,
    container: ApplicationContainer = Depends(get_container),
) -> MemoryResponse:
    """审核：accept -> active；reject -> disabled（可恢复，不删除）。"""
    try:
        record = await container.memory_governance.review_memory(
            memory_id, accept=body.accept, actor=body.actor, reason=body.reason
        )
    except ApplicationError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return MemoryResponse.model_validate(record)


@router.get("/{memory_id}/history")
async def memory_history(
    memory_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    mutations = await container.knowledge.list_mutations(memory_id)
    return [
        {
            "id": m.id,
            "action": m.action,
            "actor": m.actor,
            "reason": m.reason,
            "from_status": m.from_status,
            "to_status": m.to_status,
            "version_before": m.version_before,
            "version_after": m.version_after,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in mutations
    ]


@router.get("/{memory_id}/accesses")
async def memory_accesses(
    memory_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    events = await container.knowledge.list_access_events(memory_id)
    return [
        {
            "id": e.id,
            "run_id": e.run_id,
            "purpose": e.purpose,
            "result_count": e.result_count,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


@router.get("/{memory_id}/conflicts")
async def memory_conflicts(
    memory_id: str,
    unresolved_only: bool = Query(default=False),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    conflicts = await container.knowledge.list_conflicts(
        memory_id, unresolved_only=unresolved_only
    )
    return [
        {
            "id": c.id,
            "conflicting_memory_id": c.conflicting_memory_id,
            "content_hash": c.content_hash,
            "resolved": c.resolved,
            "resolution": c.resolution,
            "resolved_by": c.resolved_by,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        }
        for c in conflicts
    ]


@router.post("/reindex")
async def reindex_memories(
    body: ReindexRequest,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """重新索引：范围/版本/dry-run；幂等，中断后恢复不产生重复向量。"""
    return await container.memory_governance.reindex(
        scope=body.scope,
        status=body.status,
        memory_type=body.memory_type,
        dry_run=body.dry_run,
        embedding_version=body.embedding_version,
        embedder=container.embeddings.embed,
        limit=body.limit,
    )


@router.post("/maintenance")
async def memory_maintenance(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """过期扫描 + 索引一致性检查（复用生命周期机制）。"""
    return await container.memory_governance.maintenance()