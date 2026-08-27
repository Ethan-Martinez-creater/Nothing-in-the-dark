"""Memory lifecycle application services.

``MemoryExtractionService`` persists rule-extracted memory candidates with
dedup and correction override; it backs both the extract API endpoint and the
conversation-end auto extractor. ``CaseMemoryExtractor`` runs after a finished
coordinator run, mirroring the summarizer hook: idempotent per run, rule-based
(no LLM), and failures never fail the completed run.
"""

from __future__ import annotations

import logging

from app.application.memory_governance import MemoryGovernanceService
from app.application.repositories import ApplicationRepository
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.models import MemoryRecord
from app.infrastructure.embeddings import EmbeddingWorkerClient
from app.schemas.knowledge import CreateMemoryRequest
from app.services.content_security import TRUST_OPERATOR_INPUT
from app.services.memory_extraction import (
    extract_memory_candidates,
    find_related,
    find_similar,
)

logger = logging.getLogger(__name__)

_AUTO_SOURCE_PREFIX = "auto:"
_RELATED_THRESHOLD = 0.2


class MemoryExtractionService:
    """Persist extracted candidates: dedup skips near-duplicates, corrections
    (or higher-importance values) supersede the old one keeping the revision
    chain, everything else is stored as a new case-scoped memory."""

    def __init__(
        self,
        knowledge: KnowledgeRepository,
        embeddings: EmbeddingWorkerClient,
        governance: MemoryGovernanceService | None = None,
    ) -> None:
        self._knowledge = knowledge
        self._embeddings = embeddings
        # M23: 治理 Gate（写入策略/秘密扫描/冲突检测）；None 时保留旧路径。
        self._governance = governance

    async def extract_and_persist(
        self,
        *,
        case_id: str,
        text: str,
        source_type: str,
        source_id: str,
        dedup_threshold: float = 0.85,
    ) -> list[MemoryRecord]:
        candidates = extract_memory_candidates(text)
        if not candidates:
            return []

        existing = list(await self._knowledge.list_memories(case_id))
        existing_contents = [record.content for record in existing]
        try:
            embeddings = await self._embeddings.embed(
                [candidate.content for candidate in candidates]
            )
        except Exception:
            # Rule-based extraction must not depend on the embedding worker;
            # degrade to un-vectorized memories so candidates still persist.
            logger.exception(
                "embedding worker unavailable; persisting memories without vectors"
            )
            embeddings = None

        created: list[MemoryRecord] = []
        for index, candidate in enumerate(candidates):
            # 纠正类先按主题相关匹配（bigram 主题重叠），未命中再退回整句相似
            similar = (
                find_related(
                    existing_contents,
                    candidate.content,
                    threshold=_RELATED_THRESHOLD,
                )
                if candidate.kind == "correction"
                else None
            )
            if similar is None:
                similar = find_similar(
                    existing_contents,
                    candidate.content,
                    dedup_threshold,
                )
            if similar is not None:
                similar_index, _ = similar
                similar_record = existing[similar_index]
                supersedes = (
                    candidate.kind == "correction"
                    or candidate.importance > similar_record.importance
                )
                if not supersedes:
                    continue
                # 旧值移出候选池，避免后续候选反复命中同一个已覆盖值
                existing.pop(similar_index)
                existing_contents.pop(similar_index)
            else:
                similar_record = None
                supersedes = False

            request = CreateMemoryRequest(
                scope="case",
                kind=candidate.kind,
                content=candidate.content,
                source_type=source_type,
                source_id=source_id,
                importance=candidate.importance,
                supersedes_id=similar_record.id if supersedes else None,
                metadata={"extracted": True, "pattern": candidate.pattern},
            )
            if self._governance is not None:
                # M23: 提取候选来自用户话语（纠正/约束/偏好），按类型 Gate 写入；
                # 用户明确表达 + 对话 turn 即来源引用，允许成为可检索记忆。
                record = await self._governance.persist_governed(
                    case_id=case_id,
                    request=request,
                    trust_level=TRUST_OPERATOR_INPUT,
                    explicit_user_input=True,
                    has_evidence=True,
                    embedding=embeddings[index] if embeddings else None,
                    actor="memory_extractor",
                )
            else:
                record = await self._knowledge.create_memory(
                    case_id,
                    request,
                    embedding=embeddings[index] if embeddings else None,
                )
            created.append(record)
            existing.append(record)
            existing_contents.append(record.content)
        return created


class CaseMemoryExtractor:
    """Automatically extract memory candidates when a conversation run ends.

    Idempotent per source run: a run whose extraction already persisted
    records (``auto:{run_id}`` source) is never re-extracted. Re-runs on the
    same transcript are safe anyway through dedup. Exceptions are swallowed
    and recorded as a ``memory_extraction_failed`` run event.
    """

    def __init__(
        self,
        repository: ApplicationRepository,
        knowledge: KnowledgeRepository,
        service: MemoryExtractionService,
    ) -> None:
        self._repository = repository
        self._knowledge = knowledge
        self._service = service

    async def extract(self, *, case_id: str, run_id: str) -> None:
        source_id = f"{_AUTO_SOURCE_PREFIX}{run_id}"
        try:
            memories = await self._knowledge.list_memories(case_id)
            if any(
                memory.source_id == source_id and memory.active
                for memory in memories
            ):
                return
            turns = await self._repository.list_turns(case_id)
            transcript = "\n".join(
                turn.content for turn in turns if turn.role == "user"
            )
            if not transcript.strip():
                return
            await self._service.extract_and_persist(
                case_id=case_id,
                text=transcript,
                source_type="conversation",
                source_id=source_id,
            )
        except Exception:
            logger.exception("memory extraction failed for run %s", run_id)
            try:
                await self._repository.add_run_event(
                    run_id,
                    {
                        "event_type": "memory_extraction_failed",
                        "agent": "coordinator",
                        "status": "failed",
                        "error": "memory_extraction_failed",
                    },
                )
            except Exception:
                logger.exception(
                    "failed to record memory extraction error for run %s",
                    run_id,
                )
