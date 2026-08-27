from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import TEXT, TIMESTAMP, String, and_, bindparam, cast, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY

from app.core.errors import ApplicationError, ResourceNotFoundError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    ArtifactRecord,
    ClaimRecord,
    EvidenceRecord,
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    MemoryAccessEventRecord,
    MemoryConflictRecord,
    MemoryMutationRecord,
    MemoryRecord,
    SourceCommentRecord,
    SourcePostRecord,
)
from app.schemas.knowledge import CreateMemoryRequest
from app.services.memory_governance import (
    MEMORY_STATUS_ACTIVE,
    memory_type_for_kind,
)
from app.services.memory_governance import (
    content_hash as governance_content_hash,
)


@dataclass(slots=True)
class RagHit:
    evidence_id: str
    source_type: str
    source_id: str
    content: str
    score: float = 0
    retrieval_modes: list[str] = field(default_factory=list)
    platform: str | None = None
    source_url: str | None = None
    published_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def rerank_hits(
    hits: Sequence[RagHit],
    *,
    query_terms: list[str],
    limit: int,
) -> list[RagHit]:
    """Deterministic re-ranking: RRF score weighted with query-term overlap.

    No external worker or LLM call: overlap is the share of query terms that
    literally appear in the hit content. Returns up to ``limit`` hits.
    """
    if not query_terms:
        return sorted(hits, key=lambda hit: (-hit.score, hit.evidence_id))[:limit]
    ranked: list[RagHit] = []
    for hit in hits:
        content = hit.content.lower()
        overlap = sum(1 for term in query_terms if term.lower() in content)
        hit.score = round(hit.score * 0.7 + (overlap / len(query_terms)) * 0.3, 6)
        ranked.append(hit)
    return sorted(ranked, key=lambda hit: (-hit.score, hit.evidence_id))[:limit]



_EXTERNAL_SOURCE_TYPES = frozenset(
    {
        "social_post",
        "social_comment",
        "page",
        "ocr",
        "asr",
        "tool_output",
        "mcp",
        "external",
    }
)


def _trust_for_source(source_type: str) -> str:
    """来源到信任等级的确定性映射（与 ContextBuilder 一致，M16/M23）。"""
    from app.services.content_security import (
        TRUST_EXTERNAL_CONTENT,
        TRUST_GENERATED_CONTENT,
        TRUST_OPERATOR_INPUT,
        TRUST_REVIEWED_EVIDENCE,
    )

    if source_type in _EXTERNAL_SOURCE_TYPES:
        return TRUST_EXTERNAL_CONTENT
    if source_type == "conversation":
        return TRUST_GENERATED_CONTENT
    if source_type == "constraint":
        return TRUST_OPERATOR_INPUT
    return TRUST_REVIEWED_EVIDENCE

class KnowledgeRepository:
    """Case-scoped memory and evidence retrieval with PostgreSQL hybrid search."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_memory(
        self,
        case_id: str,
        request: CreateMemoryRequest,
        *,
        embedding: list[float] | None = None,
        memory_type: str | None = None,
        trust_level: str | None = None,
        review_state: str | None = None,
        status: str | None = None,
        sensitivity: str | None = None,
        expires_at: datetime | None = None,
        valid_from: datetime | None = None,
        write_policy_version: str = "1.0",
    ) -> MemoryRecord:
        """创建记忆（M23 治理字段）。去重按 内容+来源 精确匹配且非删除。"""
        resolved_type = memory_type or memory_type_for_kind(request.kind)
        resolved_trust = trust_level or _trust_for_source(request.source_type)
        resolved_review = review_state or "unreviewed"
        resolved_status = status or MEMORY_STATUS_ACTIVE
        resolved_sensitivity = sensitivity or "low"
        async with self._database.session_factory() as session:
            duplicate = await session.scalar(
                select(MemoryRecord).where(
                    MemoryRecord.case_id == case_id,
                    MemoryRecord.content == request.content,
                    MemoryRecord.source_type == request.source_type,
                    MemoryRecord.source_id == request.source_id,
                    MemoryRecord.status != "deleted",
                )
            )
            if duplicate is not None:
                return duplicate

            superseded: MemoryRecord | None = None
            if request.supersedes_id:
                superseded = await session.get(MemoryRecord, request.supersedes_id)
                if superseded is None or superseded.case_id != case_id:
                    raise ResourceNotFoundError("memory", request.supersedes_id)
                superseded.status = "superseded"
                superseded.active = False

            record = MemoryRecord(
                case_id=case_id,
                scope=request.scope,
                kind=request.kind,
                content=request.content,
                source_type=request.source_type,
                source_id=request.source_id,
                importance=request.importance,
                confidence=request.confidence,
                active=resolved_status in {"active", "pending_review"},
                supersedes_id=superseded.id if superseded else None,
                embedding=embedding,
                metadata_json=request.metadata,
                memory_type=resolved_type,
                trust_level=resolved_trust,
                review_state=resolved_review,
                confidence_level=(
                    "high" if request.confidence >= 0.7
                    else "medium" if request.confidence >= 0.4
                    else "low"
                ),
                valid_from=valid_from,
                expires_at=expires_at,
                content_hash=governance_content_hash(request.content),
                version=1,
                sensitivity=resolved_sensitivity,
                index_status="indexed" if embedding else "pending",
                write_policy_version=write_policy_version,
                status=resolved_status,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_memories(
        self,
        case_id: str | None = None,
        *,
        include_inactive: bool = False,
        scope: str | None = None,
        memory_type: str | None = None,
        status: str | None = None,
        source_type: str | None = None,
        limit: int | None = None,
    ) -> Sequence[MemoryRecord]:
        """List memories, optionally scoped.

        ``case_id=None`` returns cross-case records (domain scope); a non-null
        ``scope`` further narrows by the memory scope. Domain memories are
        never returned for a concrete case, which keeps them out of case
        context, listing and RAG search.

        M23: status / memory_type / source_type 按治理字段过滤；默认只返回
        可检索状态（active），include_inactive 显示全部非删除。
        """
        async with self._database.session_factory() as session:
            query = select(MemoryRecord)
            if case_id is not None:
                query = query.where(MemoryRecord.case_id == case_id)
            if scope is not None:
                query = query.where(MemoryRecord.scope == scope)
            if status is not None:
                query = query.where(MemoryRecord.status == status)
            elif not include_inactive:
                query = query.where(
                    MemoryRecord.status.in_(("active", "pending_review"))
                )
            if memory_type is not None:
                query = query.where(MemoryRecord.memory_type == memory_type)
            if source_type is not None:
                query = query.where(MemoryRecord.source_type == source_type)
            if limit is not None:
                query = query.limit(limit)
            result = await session.scalars(
                query.order_by(
                    MemoryRecord.importance.desc(),
                    MemoryRecord.updated_at.desc(),
                )
            )
            return result.all()

    async def update_memory_active(
        self,
        memory_id: str,
        active: bool,
    ) -> MemoryRecord | None:
        async with self._database.session_factory() as session:
            record = await session.get(MemoryRecord, memory_id)
            if record is None:
                return None
            record.active = active
            await session.commit()
            await session.refresh(record)
            return record

    async def add_document(
        self,
        *,
        case_id: str,
        filename: str,
        media_type: str,
        checksum: str,
        chunks: Sequence[str],
        metadata: dict[str, Any] | None = None,
        embeddings: Sequence[list[float] | None] | None = None,
    ) -> KnowledgeDocumentRecord:
        if embeddings is not None and len(embeddings) != len(chunks):
            raise ApplicationError(
                "Embedding count does not match chunk count",
                code="embedding_count_mismatch",
            )
        async with self._database.session_factory() as session:
            existing = await session.scalar(
                select(KnowledgeDocumentRecord).where(
                    KnowledgeDocumentRecord.case_id == case_id,
                    KnowledgeDocumentRecord.checksum == checksum,
                )
            )
            if existing is not None:
                return existing

            document = KnowledgeDocumentRecord(
                case_id=case_id,
                filename=filename,
                media_type=media_type,
                checksum=checksum,
                status="ready",
                metadata_json=metadata or {},
            )
            session.add(document)
            await session.flush()
            for ordinal, content in enumerate(chunks):
                session.add(
                    KnowledgeChunkRecord(
                        document_id=document.id,
                        ordinal=ordinal,
                        content=content,
                        token_count=max(1, len(content) // 4),
                        embedding=embeddings[ordinal] if embeddings else None,
                        metadata_json={"filename": filename},
                    )
                )
            await session.commit()
            await session.refresh(document)
            return document

    async def search(
        self,
        *,
        case_id: str,
        query: str,
        limit: int,
        embedding: list[float] | None = None,
        source_types: set[str] | None = None,
        platforms: list[str] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
    ) -> list[RagHit]:
        if self._database.engine.dialect.name != "postgresql":
            return await self._search_sqlite(
                case_id=case_id,
                query=query,
                limit=limit,
                source_types=source_types,
                platforms=platforms,
                time_from=time_from,
                time_to=time_to,
            )

        keyword_hits = await self._search_postgres_keyword(
            case_id=case_id,
            query=query,
            limit=limit * 3,
            platforms=platforms,
            time_from=time_from,
            time_to=time_to,
        )
        vector_hits: list[RagHit] = []
        if embedding is not None:
            vector_hits = await self._search_postgres_vector(
                case_id=case_id,
                embedding=embedding,
                limit=limit * 3,
                platforms=platforms,
                time_from=time_from,
                time_to=time_to,
            )
        return self._rrf(
            keyword_hits,
            vector_hits,
            limit=limit,
            source_types=source_types,
            query_terms=self._query_terms(query),
        )

    async def _search_postgres_keyword(
        self,
        *,
        case_id: str,
        query: str,
        limit: int,
        platforms: list[str] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
    ) -> list[RagHit]:
        statement = text(
            """
            SELECT * FROM (
                SELECT
                    'memory' AS source_type,
                    id AS source_id,
                    content,
                    NULL::text AS platform,
                    NULL::text AS source_url,
                    NULL::timestamptz AS published_at,
                    ts_rank_cd(search_vector, plainto_tsquery('simple', :query))
                        + CASE
                            WHEN content ILIKE ALL(CAST(:patterns AS text[]))
                            THEN 0.25 ELSE 0
                          END AS rank
                FROM memories
                WHERE case_id = :case_id AND active
                  AND (
                    search_vector @@ plainto_tsquery('simple', :query)
                    OR content ILIKE ALL(CAST(:patterns AS text[]))
                  )
                UNION ALL
                SELECT
                    'social_post',
                    id,
                    content,
                    platform,
                    source_url,
                    published_at,
                    ts_rank_cd(search_vector, plainto_tsquery('simple', :query))
                        + CASE
                            WHEN content ILIKE ALL(CAST(:patterns AS text[]))
                            THEN 0.25 ELSE 0
                          END
                FROM source_posts
                WHERE case_id = :case_id
                  AND (
                    search_vector @@ plainto_tsquery('simple', :query)
                    OR content ILIKE ALL(CAST(:patterns AS text[]))
                  )
                  AND (:platforms IS NULL OR platform = ANY(CAST(:platforms AS text[])))
                  AND (:time_from IS NULL OR published_at >= :time_from)
                  AND (:time_to IS NULL OR published_at <= :time_to)
                UNION ALL
                SELECT
                    'document_chunk',
                    kc.id,
                    kc.content,
                    NULL::text,
                    NULL::text,
                    NULL::timestamptz,
                    ts_rank_cd(kc.search_vector, plainto_tsquery('simple', :query))
                        + CASE
                            WHEN kc.content ILIKE ALL(CAST(:patterns AS text[]))
                            THEN 0.25 ELSE 0
                          END
                FROM knowledge_chunks kc
                JOIN knowledge_documents kd ON kd.id = kc.document_id
                WHERE kd.case_id = :case_id AND kd.status = 'ready'
                  AND (
                    kc.search_vector @@ plainto_tsquery('simple', :query)
                    OR kc.content ILIKE ALL(CAST(:patterns AS text[]))
                  )
                UNION ALL
                SELECT
                    'social_comment',
                    c.id,
                    c.content,
                    c.platform,
                    p.source_url,
                    c.published_at,
                    ts_rank_cd(c.search_vector, plainto_tsquery('simple', :query))
                        + CASE
                            WHEN c.content ILIKE ALL(CAST(:patterns AS text[]))
                            THEN 0.25 ELSE 0
                          END
                FROM source_comments c
                JOIN source_posts p ON p.id = c.post_id
                WHERE p.case_id = :case_id
                  AND (
                    c.search_vector @@ plainto_tsquery('simple', :query)
                    OR c.content ILIKE ALL(CAST(:patterns AS text[]))
                  )
                  AND (:platforms IS NULL OR c.platform = ANY(CAST(:platforms AS text[])))
                  AND (:time_from IS NULL OR c.published_at >= :time_from)
                  AND (:time_to IS NULL OR c.published_at <= :time_to)
                UNION ALL
                SELECT
                    'artifact',
                    id,
                    COALESCE(title, '') || ' ' || COALESCE(data::text, ''),
                    NULL::text,
                    NULL::text,
                    NULL::timestamptz,
                    ts_rank_cd(search_vector, plainto_tsquery('simple', :query))
                        + CASE
                            WHEN (COALESCE(title, '') || ' ' || COALESCE(data::text, ''))
                                 ILIKE ALL(CAST(:patterns AS text[]))
                            THEN 0.25 ELSE 0
                          END
                FROM artifacts
                WHERE case_id = :case_id
                  AND (
                    search_vector @@ plainto_tsquery('simple', :query)
                    OR (COALESCE(title, '') || ' ' || COALESCE(data::text, ''))
                       ILIKE ALL(CAST(:patterns AS text[]))
                  )
                UNION ALL
                SELECT
                    'claim',
                    id,
                    text,
                    NULL::text,
                    NULL::text,
                    NULL::timestamptz,
                    ts_rank_cd(search_vector, plainto_tsquery('simple', :query))
                        + CASE
                            WHEN text ILIKE ALL(CAST(:patterns AS text[]))
                            THEN 0.25 ELSE 0
                          END
                FROM claims
                WHERE case_id = :case_id
                  AND (
                    search_vector @@ plainto_tsquery('simple', :query)
                    OR text ILIKE ALL(CAST(:patterns AS text[]))
                  )
                UNION ALL
                SELECT
                    'evidence',
                    id,
                    excerpt,
                    NULL::text,
                    NULL::text,
                    NULL::timestamptz,
                    ts_rank_cd(search_vector, plainto_tsquery('simple', :query))
                        + CASE
                            WHEN excerpt ILIKE ALL(CAST(:patterns AS text[]))
                            THEN 0.25 ELSE 0
                          END
                FROM evidence
                WHERE case_id = :case_id
                  AND (
                    search_vector @@ plainto_tsquery('simple', :query)
                    OR excerpt ILIKE ALL(CAST(:patterns AS text[]))
                  )
            ) ranked
            ORDER BY rank DESC, source_id
            LIMIT :limit
            """
        ).bindparams(
            # Explicit types so asyncpg can prepare the statement even when
            # the filter values are NULL (it cannot infer the type of $4..$6).
            bindparam("platforms", type_=ARRAY(TEXT)),
            bindparam("time_from", type_=TIMESTAMP(timezone=True)),
            bindparam("time_to", type_=TIMESTAMP(timezone=True)),
        )
        async with self._database.session_factory() as session:
            rows = (
                await session.execute(
                    statement,
                    {
                        "case_id": case_id,
                        "query": query,
                        "patterns": self._keyword_patterns(query),
                        "platforms": platforms,
                        "time_from": time_from,
                        "time_to": time_to,
                        "limit": limit,
                    },
                )
            ).mappings()
            return [
                self._row_to_hit(row, retrieval_mode="keyword")
                for row in rows
            ]

    async def _search_postgres_vector(
        self,
        *,
        case_id: str,
        embedding: list[float],
        limit: int,
        platforms: list[str] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
    ) -> list[RagHit]:
        if len(embedding) != 1024:
            raise ApplicationError(
                f"Expected a 1024-dimensional embedding, got {len(embedding)}",
                code="invalid_embedding_dimensions",
            )
        statement = text(
            """
            SELECT * FROM (
                SELECT
                    'memory' AS source_type,
                    id AS source_id,
                    content,
                    NULL::text AS platform,
                    NULL::text AS source_url,
                    NULL::timestamptz AS published_at,
                    1 - (embedding <=> CAST(:embedding AS vector)) AS rank
                FROM memories
                WHERE case_id = :case_id AND active AND embedding IS NOT NULL
                UNION ALL
                SELECT
                    'social_post',
                    id,
                    content,
                    platform,
                    source_url,
                    published_at,
                    1 - (embedding <=> CAST(:embedding AS vector))
                FROM source_posts
                WHERE case_id = :case_id AND embedding IS NOT NULL
                  AND (:platforms IS NULL OR platform = ANY(CAST(:platforms AS text[])))
                  AND (:time_from IS NULL OR published_at >= :time_from)
                  AND (:time_to IS NULL OR published_at <= :time_to)
                UNION ALL
                SELECT
                    'document_chunk',
                    kc.id,
                    kc.content,
                    NULL::text,
                    NULL::text,
                    NULL::timestamptz,
                    1 - (kc.embedding <=> CAST(:embedding AS vector))
                FROM knowledge_chunks kc
                JOIN knowledge_documents kd ON kd.id = kc.document_id
                WHERE kd.case_id = :case_id AND kd.status = 'ready'
                  AND kc.embedding IS NOT NULL
                UNION ALL
                SELECT
                    'social_comment',
                    c.id,
                    c.content,
                    c.platform,
                    p.source_url,
                    c.published_at,
                    1 - (c.embedding <=> CAST(:embedding AS vector))
                FROM source_comments c
                JOIN source_posts p ON p.id = c.post_id
                WHERE p.case_id = :case_id AND c.embedding IS NOT NULL
                  AND (:platforms IS NULL OR c.platform = ANY(CAST(:platforms AS text[])))
                  AND (:time_from IS NULL OR c.published_at >= :time_from)
                  AND (:time_to IS NULL OR c.published_at <= :time_to)
                UNION ALL
                SELECT
                    'artifact',
                    id,
                    COALESCE(title, '') || ' ' || COALESCE(data::text, ''),
                    NULL::text,
                    NULL::text,
                    NULL::timestamptz,
                    1 - (embedding <=> CAST(:embedding AS vector))
                FROM artifacts
                WHERE case_id = :case_id AND embedding IS NOT NULL
                UNION ALL
                SELECT
                    'claim',
                    id,
                    text,
                    NULL::text,
                    NULL::text,
                    NULL::timestamptz,
                    1 - (embedding <=> CAST(:embedding AS vector))
                FROM claims
                WHERE case_id = :case_id AND embedding IS NOT NULL
                UNION ALL
                SELECT
                    'evidence',
                    id,
                    excerpt,
                    NULL::text,
                    NULL::text,
                    NULL::timestamptz,
                    1 - (embedding <=> CAST(:embedding AS vector))
                FROM evidence
                WHERE case_id = :case_id AND embedding IS NOT NULL
            ) ranked
            ORDER BY rank DESC, source_id
            LIMIT :limit
            """
        ).bindparams(
            # Explicit types so asyncpg can prepare the statement even when
            # the filter values are NULL (it cannot infer the type of $4..$6).
            bindparam("platforms", type_=ARRAY(TEXT)),
            bindparam("time_from", type_=TIMESTAMP(timezone=True)),
            bindparam("time_to", type_=TIMESTAMP(timezone=True)),
        )
        vector_literal = "[" + ",".join(str(value) for value in embedding) + "]"
        async with self._database.session_factory() as session:
            rows = (
                await session.execute(
                    statement,
                    {
                        "case_id": case_id,
                        "embedding": vector_literal,
                        "platforms": platforms,
                        "time_from": time_from,
                        "time_to": time_to,
                        "limit": limit,
                    },
                )
            ).mappings()
            return [self._row_to_hit(row, retrieval_mode="vector") for row in rows]

    async def _search_sqlite(
        self,
        *,
        case_id: str,
        query: str,
        limit: int,
        source_types: set[str] | None,
        platforms: list[str] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
    ) -> list[RagHit]:
        patterns = self._keyword_patterns(query)
        hits: list[RagHit] = []
        async with self._database.session_factory() as session:
            if source_types is None or "memory" in source_types:
                memories = await session.scalars(
                    select(MemoryRecord)
                    .where(
                        MemoryRecord.case_id == case_id,
                        MemoryRecord.active.is_(True),
                        and_(
                            *(
                                MemoryRecord.content.ilike(pattern)
                                for pattern in patterns
                            )
                        ),
                    )
                    .limit(limit)
                )
                hits.extend(
                    RagHit(
                        evidence_id=f"memory:{record.id}",
                        source_type="memory",
                        source_id=record.id,
                        content=record.content,
                        score=1,
                        retrieval_modes=["keyword"],
                    )
                    for record in memories
                )
            if source_types is None or "social_post" in source_types:
                posts = await session.scalars(
                    select(SourcePostRecord)
                    .where(
                        SourcePostRecord.case_id == case_id,
                        and_(
                            *(
                                or_(
                                    SourcePostRecord.content.ilike(pattern),
                                    SourcePostRecord.title.ilike(pattern),
                                )
                                for pattern in patterns
                            )
                        ),
                        *self._sqlite_filters(
                            model=SourcePostRecord,
                            platforms=platforms,
                            time_from=time_from,
                            time_to=time_to,
                        ),
                    )
                    .limit(limit)
                )
                hits.extend(
                    RagHit(
                        evidence_id=f"social_post:{record.id}",
                        source_type="social_post",
                        source_id=record.id,
                        content=record.content,
                        score=1,
                        retrieval_modes=["keyword"],
                        platform=record.platform,
                        source_url=record.source_url,
                        published_at=record.published_at,
                    )
                    for record in posts
                )
            if source_types is None or "document_chunk" in source_types:
                chunks = await session.execute(
                    select(KnowledgeChunkRecord)
                    .join(
                        KnowledgeDocumentRecord,
                        KnowledgeDocumentRecord.id == KnowledgeChunkRecord.document_id,
                    )
                    .where(
                        KnowledgeDocumentRecord.case_id == case_id,
                        KnowledgeDocumentRecord.status == "ready",
                        and_(
                            *(
                                KnowledgeChunkRecord.content.ilike(pattern)
                                for pattern in patterns
                            )
                        ),
                    )
                    .limit(limit)
                )
                hits.extend(
                    RagHit(
                        evidence_id=f"document_chunk:{record.id}",
                        source_type="document_chunk",
                        source_id=record.id,
                        content=record.content,
                        score=1,
                        retrieval_modes=["keyword"],
                    )
                    for record in chunks.scalars()
                )
            if source_types is None or "social_comment" in source_types:
                comment_rows = await session.execute(
                    select(SourceCommentRecord, SourcePostRecord.source_url)
                    .join(
                        SourcePostRecord,
                        SourcePostRecord.id == SourceCommentRecord.post_id,
                    )
                    .where(
                        SourcePostRecord.case_id == case_id,
                        and_(
                            *(
                                SourceCommentRecord.content.ilike(pattern)
                                for pattern in patterns
                            )
                        ),
                        *self._sqlite_filters(
                            model=SourceCommentRecord,
                            platforms=platforms,
                            time_from=time_from,
                            time_to=time_to,
                        ),
                    )
                    .limit(limit)
                )
                hits.extend(
                    RagHit(
                        evidence_id=f"social_comment:{record.id}",
                        source_type="social_comment",
                        source_id=record.id,
                        content=record.content,
                        score=1,
                        retrieval_modes=["keyword"],
                        platform=record.platform,
                        source_url=source_url,
                        published_at=record.published_at,
                    )
                    for record, source_url in comment_rows
                )
            if source_types is None or "artifact" in source_types:
                artifacts = await session.scalars(
                    select(ArtifactRecord).where(
                        ArtifactRecord.case_id == case_id,
                        and_(
                            *(
                                or_(
                                    ArtifactRecord.title.ilike(pattern),
                                    cast(ArtifactRecord.data, String).ilike(pattern),
                                )
                                for pattern in patterns
                            )
                        ),
                    ).limit(limit)
                )
                hits.extend(
                    RagHit(
                        evidence_id=f"artifact:{record.id}",
                        source_type="artifact",
                        source_id=record.id,
                        content=(
                            f"{record.title}\n"
                            f"{json.dumps(record.data, ensure_ascii=False, default=str)[:500]}"
                        ),
                        score=1,
                        retrieval_modes=["keyword"],
                        metadata={"artifact_version": record.version},
                    )
                    for record in artifacts
                )
            if source_types is None or "claim" in source_types:
                claims = await session.scalars(
                    select(ClaimRecord).where(
                        ClaimRecord.case_id == case_id,
                        and_(
                            *(ClaimRecord.text.ilike(pattern) for pattern in patterns)
                        ),
                    ).limit(limit)
                )
                hits.extend(
                    RagHit(
                        evidence_id=f"claim:{record.id}",
                        source_type="claim",
                        source_id=record.id,
                        content=record.text,
                        score=1,
                        retrieval_modes=["keyword"],
                    )
                    for record in claims
                )
            if source_types is None or "evidence" in source_types:
                evidence_rows = await session.scalars(
                    select(EvidenceRecord).where(
                        EvidenceRecord.case_id == case_id,
                        and_(
                            *(
                                EvidenceRecord.excerpt.ilike(pattern)
                                for pattern in patterns
                            )
                        ),
                    ).limit(limit)
                )
                hits.extend(
                    RagHit(
                        evidence_id=f"evidence:{record.id}",
                        source_type="evidence",
                        source_id=record.id,
                        content=record.excerpt,
                        score=1,
                        retrieval_modes=["keyword"],
                        metadata={"stance": record.stance},
                    )
                    for record in evidence_rows
                )
        return rerank_hits(
            hits,
            query_terms=self._query_terms(query),
            limit=limit,
        )

    @staticmethod
    def _sqlite_filters(
        *,
        model: Any,
        platforms: list[str] | None,
        time_from: datetime | None,
        time_to: datetime | None,
    ) -> list[Any]:
        filters: list[Any] = []
        if platforms is not None:
            filters.append(model.platform.in_(platforms))
        if time_from is not None:
            filters.append(model.published_at >= time_from)
        if time_to is not None:
            filters.append(model.published_at <= time_to)
        return filters

    @staticmethod
    def _row_to_hit(row: Any, *, retrieval_mode: str) -> RagHit:
        source_type = str(row["source_type"])
        source_id = str(row["source_id"])
        return RagHit(
            evidence_id=f"{source_type}:{source_id}",
            source_type=source_type,
            source_id=source_id,
            content=str(row["content"]),
            score=float(row["rank"] or 0),
            retrieval_modes=[retrieval_mode],
            platform=str(row["platform"]) if row["platform"] else None,
            source_url=str(row["source_url"]) if row["source_url"] else None,
            published_at=row["published_at"],
        )

    @staticmethod
    def _keyword_patterns(query: str) -> list[str]:
        terms = list(dict.fromkeys(term for term in query.split() if term))
        if not terms:
            terms = [query]
        return [f"%{term}%" for term in terms]

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        return list(dict.fromkeys(term for term in query.split() if term))

    @staticmethod
    def _rrf(
        keyword_hits: Sequence[RagHit],
        vector_hits: Sequence[RagHit],
        *,
        limit: int,
        source_types: set[str] | None,
        query_terms: list[str],
        k: int = 60,
    ) -> list[RagHit]:
        merged: dict[str, RagHit] = {}
        for mode, ranking in (("keyword", keyword_hits), ("vector", vector_hits)):
            for rank, hit in enumerate(ranking, start=1):
                if source_types is not None and hit.source_type not in source_types:
                    continue
                current = merged.get(hit.evidence_id)
                if current is None:
                    current = hit
                    current.score = 0
                    current.retrieval_modes = []
                    merged[hit.evidence_id] = current
                current.score += 1 / (k + rank)
                if mode not in current.retrieval_modes:
                    current.retrieval_modes.append(mode)
        return rerank_hits(
            merged.values(),
            query_terms=query_terms,
            limit=limit,
        )
    # ---- M23: 记忆治理（状态机 / 审计 / 冲突 / 访问 / 维护） ----

    async def get_memory(self, memory_id: str) -> MemoryRecord | None:
        async with self._database.session_factory() as session:
            return await session.get(MemoryRecord, memory_id)

    async def set_memory_status(
        self,
        memory_id: str,
        *,
        new_status: str,
        action: str,
        actor: str = "operator",
        reason: str = "",
    ) -> MemoryRecord | None:
        """机械状态迁移 + mutation 审计（转移合法性由服务层校验）。"""
        async with self._database.session_factory() as session:
            record = await session.get(MemoryRecord, memory_id)
            if record is None:
                return None
            from_status = record.status
            session.add(
                MemoryMutationRecord(
                    memory_id=record.id,
                    action=action,
                    actor=actor,
                    reason=reason,
                    from_status=from_status,
                    to_status=new_status,
                    version_before=record.version,
                    version_after=record.version,
                )
            )
            record.status = new_status
            record.active = new_status in {"active", "pending_review"}
            await session.commit()
            await session.refresh(record)
            return record

    async def apply_memory_correction(
        self,
        memory_id: str,
        request: CreateMemoryRequest,
        *,
        actor: str = "operator",
        reason: str = "",
        embedding: list[float] | None = None,
        memory_type: str | None = None,
        trust_level: str | None = None,
        review_state: str | None = None,
        status: str | None = None,
        sensitivity: str | None = None,
        expires_at: datetime | None = None,
        write_policy_version: str = "1.0",
    ) -> MemoryRecord | None:
        """修正产生新版本：旧记录 superseded，新记录 version+1。"""
        async with self._database.session_factory() as session:
            old = await session.get(MemoryRecord, memory_id)
            if old is None:
                return None
            old.status = "superseded"
            old.active = False
            session.add(
                MemoryMutationRecord(
                    memory_id=old.id,
                    action="correct",
                    actor=actor,
                    reason=reason,
                    from_status="active",
                    to_status="superseded",
                    version_before=old.version,
                    version_after=old.version + 1,
                )
            )
            resolved_type = memory_type or memory_type_for_kind(request.kind)
            resolved_trust = trust_level or _trust_for_source(request.source_type)
            resolved_review = review_state or old.review_state
            resolved_status = status or "active"
            record = MemoryRecord(
                case_id=old.case_id,
                scope=request.scope or old.scope,
                kind=request.kind or old.kind,
                content=request.content,
                source_type=request.source_type or old.source_type,
                source_id=request.source_id or old.source_id,
                importance=request.importance,
                confidence=request.confidence,
                active=resolved_status in {"active", "pending_review"},
                supersedes_id=old.id,
                embedding=embedding,
                metadata_json=request.metadata,
                memory_type=resolved_type,
                trust_level=resolved_trust,
                review_state=resolved_review,
                confidence_level=(
                    "high" if request.confidence >= 0.7
                    else "medium" if request.confidence >= 0.4
                    else "low"
                ),
                valid_from=old.valid_from,
                expires_at=expires_at or old.expires_at,
                content_hash=governance_content_hash(request.content),
                version=old.version + 1,
                sensitivity=sensitivity or old.sensitivity or "low",
                index_status="indexed" if embedding else "pending",
                write_policy_version=write_policy_version,
                status=resolved_status,
            )
            session.add(record)
            await session.flush()  # 先生成新记录 id，再写审计
            session.add(
                MemoryMutationRecord(
                    memory_id=record.id,
                    action="correct",
                    actor=actor,
                    reason="new version",
                    from_status="",
                    to_status=resolved_status,
                    version_before=0,
                    version_after=record.version,
                )
            )
            await session.commit()
            await session.refresh(record)
            return record

    async def add_access_event(
        self,
        memory_id: str,
        *,
        run_id: str | None = None,
        purpose: str = "context",
        result_count: int = 1,
    ) -> None:
        async with self._database.session_factory() as session:
            session.add(
                MemoryAccessEventRecord(
                    memory_id=memory_id,
                    run_id=run_id,
                    purpose=purpose,
                    result_count=result_count,
                )
            )
            await session.commit()

    async def list_access_events(
        self, memory_id: str, limit: int = 50
    ) -> Sequence[MemoryAccessEventRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(MemoryAccessEventRecord)
                .where(MemoryAccessEventRecord.memory_id == memory_id)
                .order_by(MemoryAccessEventRecord.created_at.desc())
                .limit(limit)
            )
            return result.all()

    async def list_mutations(
        self, memory_id: str, limit: int = 100
    ) -> Sequence[MemoryMutationRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(MemoryMutationRecord)
                .where(MemoryMutationRecord.memory_id == memory_id)
                .order_by(MemoryMutationRecord.created_at.desc())
                .limit(limit)
            )
            return result.all()

    async def add_conflict(
        self,
        memory_id: str,
        conflicting_memory_id: str,
        *,
        content_hash: str = "",
    ) -> MemoryConflictRecord | None:
        """记录冲突（唯一对，幂等）；已有则返回 None。"""
        async with self._database.session_factory() as session:
            existing = await session.scalar(
                select(MemoryConflictRecord).where(
                    MemoryConflictRecord.memory_id == memory_id,
                    MemoryConflictRecord.conflicting_memory_id
                    == conflicting_memory_id,
                )
            )
            if existing is not None:
                return None
            record = MemoryConflictRecord(
                memory_id=memory_id,
                conflicting_memory_id=conflicting_memory_id,
                content_hash=content_hash,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_conflicts(
        self, memory_id: str, unresolved_only: bool = False
    ) -> Sequence[MemoryConflictRecord]:
        async with self._database.session_factory() as session:
            query = select(MemoryConflictRecord).where(
                MemoryConflictRecord.memory_id == memory_id,
            )
            if unresolved_only:
                query = query.where(MemoryConflictRecord.resolved.is_(False))
            result = await session.scalars(
                query.order_by(MemoryConflictRecord.created_at.desc())
            )
            return result.all()

    async def resolve_conflict(
        self,
        conflict_id: str,
        *,
        resolution: str,
        resolved_by: str = "operator",
    ) -> MemoryConflictRecord | None:
        async with self._database.session_factory() as session:
            record = await session.get(MemoryConflictRecord, conflict_id)
            if record is None:
                return None
            record.resolved = True
            record.resolution = resolution
            record.resolved_by = resolved_by
            record.resolved_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(record)
            return record

    async def scan_expired_memories(
        self, now: datetime
    ) -> list[MemoryRecord]:
        """过期扫描：expires_at 已到且仍可检索的记忆 -> expired（记录 mutation）。"""
        expired: list[MemoryRecord] = []
        async with self._database.session_factory() as session:
            records = await session.scalars(
                select(MemoryRecord).where(
                    MemoryRecord.expires_at.is_not(None),
                    MemoryRecord.expires_at < now,
                    MemoryRecord.status.in_(("active", "pending_review")),
                )
            )
            for record in records.all():
                session.add(
                    MemoryMutationRecord(
                        memory_id=record.id,
                        action="expire",
                        actor="maintenance",
                        reason="expires_at reached",
                        from_status=record.status,
                        to_status="expired",
                        version_before=record.version,
                        version_after=record.version,
                    )
                )
                record.status = "expired"
                record.active = False
                expired.append(record)
            await session.commit()
        return expired

    async def list_index_stale_memories(
        self, limit: int = 200
    ) -> Sequence[MemoryRecord]:
        """索引一致性：status 可检索但 index_status != indexed（缺向量）。"""
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(MemoryRecord)
                .where(
                    MemoryRecord.status.in_(("active", "pending_review")),
                    MemoryRecord.index_status != "indexed",
                )
                .limit(limit)
            )
            return result.all()

    async def mark_memory_indexed(
        self, memory_id: str, *, embedding: list[float], embedding_version: str
    ) -> MemoryRecord | None:
        async with self._database.session_factory() as session:
            record = await session.get(MemoryRecord, memory_id)
            if record is None:
                return None
            record.embedding = embedding
            record.index_status = "indexed"
            record.embedding_version = embedding_version
            await session.commit()
            await session.refresh(record)
            return record

    async def set_memory_review_state(
        self,
        memory_id: str,
        *,
        review_state: str,
        last_verified_at: datetime | None = None,
    ) -> MemoryRecord | None:
        async with self._database.session_factory() as session:
            record = await session.get(MemoryRecord, memory_id)
            if record is None:
                return None
            record.review_state = review_state
            if last_verified_at is not None:
                record.last_verified_at = last_verified_at
            await session.commit()
            await session.refresh(record)
            return record
