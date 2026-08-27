from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.knowledge import (
    CreateMemoryRequest,
    DecayMemoryRequest,
    DecayResultResponse,
    DocumentResponse,
    ExtractMemoryRequest,
    MemoryResponse,
    MemorySearchRequest,
    RagHitResponse,
)
from app.services.documents import (
    chunk_document,
    document_checksum,
    extract_document_text,
)
from app.services.memory_extraction import should_decay

router = APIRouter()

#: 用户显式来源 -> operator_input（人工确认路径）。
_OPERATOR_SOURCE_TYPES = frozenset(
    {"user", "operator", "user_correction", "constraint", "preference"}
)


def _operator_trust(source_type: str) -> str:
    from app.services.content_security import (
        TRUST_EXTERNAL_CONTENT,
        TRUST_OPERATOR_INPUT,
        TRUST_REVIEWED_EVIDENCE,
    )

    if source_type in _OPERATOR_SOURCE_TYPES:
        return TRUST_OPERATOR_INPUT
    if source_type in {"social_post", "social_comment", "page", "external"}:
        return TRUST_EXTERNAL_CONTENT
    if source_type == "conversation":
        return TRUST_REVIEWED_EVIDENCE
    return TRUST_REVIEWED_EVIDENCE


@router.get("/{case_id}/memories", response_model=list[MemoryResponse])
async def list_memories(
    case_id: str,
    include_inactive: bool = Query(default=False),
    container: ApplicationContainer = Depends(get_container),
) -> list[MemoryResponse]:
    await container.repository.get_case(case_id)
    records = await container.knowledge.list_memories(
        case_id,
        include_inactive=include_inactive,
    )
    return [MemoryResponse.model_validate(record) for record in records]


@router.post(
    "/{case_id}/memories",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_memory(
    case_id: str,
    request: CreateMemoryRequest,
    container: ApplicationContainer = Depends(get_container),
) -> MemoryResponse:
    """创建记忆（M23 治理 Gate）：用户显式写入视为 operator_input 人工确认。"""
    await container.repository.get_case(case_id)
    vectors = await container.embeddings.embed([request.content])
    record = await container.memory_governance.persist_governed(
        case_id=case_id,
        request=request,
        trust_level=_operator_trust(request.source_type),
        explicit_user_input=True,
        has_evidence=True,
        embedding=vectors[0] if vectors else None,
        actor="operator",
    )
    return MemoryResponse.model_validate(record)


@router.post(
    "/{case_id}/memories/extract",
    response_model=list[MemoryResponse],
    status_code=status.HTTP_201_CREATED,
)
async def extract_memories(
    case_id: str,
    request: ExtractMemoryRequest,
    container: ApplicationContainer = Depends(get_container),
) -> list[MemoryResponse]:
    """从对话文本提取 Memory 候选：指令/偏好入库，纠正类自动覆盖旧值
    （保留 supersedes 修订链），相似内容去重跳过。"""
    await container.repository.get_case(case_id)
    text_hash = hashlib.sha1(request.text.encode("utf-8")).hexdigest()[:12]
    records = await container.memory_service.extract_and_persist(
        case_id=case_id,
        text=request.text,
        source_type=request.source_type,
        source_id=request.source_id or f"extract:{text_hash}",
        dedup_threshold=request.dedup_threshold,
    )
    return [MemoryResponse.model_validate(record) for record in records]


@router.post(
    "/{case_id}/memories/decay",
    response_model=DecayResultResponse,
)
async def decay_memories(
    case_id: str,
    request: DecayMemoryRequest,
    container: ApplicationContainer = Depends(get_container),
) -> DecayResultResponse:
    """失效低重要性且长期未更新的 Memory（标记 active=False）。"""
    await container.repository.get_case(case_id)
    records = await container.knowledge.list_memories(
        case_id,
        include_inactive=True,
    )
    now = datetime.now(UTC)
    deactivated = 0
    for record in records:
        if record.active and should_decay(
            record.updated_at,
            record.importance,
            now=now,
            ttl_days=request.ttl_days,
            min_importance=request.min_importance,
        ):
            await container.knowledge.update_memory_active(record.id, False)
            deactivated += 1
    return DecayResultResponse(deactivated=deactivated)


@router.post(
    "/{case_id}/memory/search",
    response_model=list[RagHitResponse],
)
async def search_case_memory(
    case_id: str,
    request: MemorySearchRequest,
    container: ApplicationContainer = Depends(get_container),
) -> list[RagHitResponse]:
    await container.repository.get_case(case_id)
    vectors = await container.embeddings.embed([request.query])
    time_from = time_to = None
    if request.time_range:
        if request.time_range.get("from"):
            time_from = datetime.fromisoformat(request.time_range["from"])
        if request.time_range.get("to"):
            time_to = datetime.fromisoformat(request.time_range["to"])
    hits = await container.knowledge.search(
        case_id=case_id,
        query=request.query,
        limit=request.limit,
        embedding=vectors[0] if vectors else None,
        source_types={"memory"},
        platforms=request.platforms,
        time_from=time_from,
        time_to=time_to,
    )
    return [RagHitResponse.model_validate(hit) for hit in hits]


@router.post(
    "/{case_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    case_id: str,
    file: Annotated[UploadFile, File()],
    source_url: Annotated[str | None, Form()] = None,
    container: ApplicationContainer = Depends(get_container),
) -> DocumentResponse:
    await container.repository.get_case(case_id)
    content = await file.read()
    filename = file.filename or "document"
    media_type = file.content_type or "application/octet-stream"
    text = extract_document_text(
        content,
        filename=filename,
        media_type=media_type,
    )
    chunks = chunk_document(text)
    embeddings = await container.embeddings.embed(chunks)
    record = await container.knowledge.add_document(
        case_id=case_id,
        filename=filename,
        media_type=media_type,
        checksum=document_checksum(content),
        chunks=chunks,
        metadata={"source_url": source_url} if source_url else {},
        embeddings=embeddings,
    )
    return DocumentResponse.model_validate(record)


@router.post(
    "/{case_id}/evidence/search",
    response_model=list[RagHitResponse],
)
async def search_case_evidence(
    case_id: str,
    request: MemorySearchRequest,
    container: ApplicationContainer = Depends(get_container),
) -> list[RagHitResponse]:
    await container.repository.get_case(case_id)
    vectors = await container.embeddings.embed([request.query])
    time_from = time_to = None
    if request.time_range:
        if request.time_range.get("from"):
            time_from = datetime.fromisoformat(request.time_range["from"])
        if request.time_range.get("to"):
            time_to = datetime.fromisoformat(request.time_range["to"])
    hits = await container.knowledge.search(
        case_id=case_id,
        query=request.query,
        limit=request.limit,
        embedding=vectors[0] if vectors else None,
        platforms=request.platforms,
        time_from=time_from,
        time_to=time_to,
    )
    return [RagHitResponse.model_validate(hit) for hit in hits]
