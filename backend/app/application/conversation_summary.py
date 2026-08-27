"""Conversation summarization written back as case-scoped summary memory."""

from __future__ import annotations

import logging

from app.application.memory_governance import MemoryGovernanceService
from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.llm import LLMGateway, LLMMessage, ModelRoute
from app.schemas.knowledge import CreateMemoryRequest
from app.services.content_security import TRUST_GENERATED_CONTENT
from app.services.memory_governance import (
    MEMORY_TYPE_CONVERSATION_SUMMARY,
    summary_tag,
)

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = (
    "你是对话摘要助手。请把以下案例对话压缩为一份中文摘要，"
    "保留：用户确认的关键约束、研究范围、重要事实与结论。"
    "省略寒暄和无关细节。摘要控制在 {max_tokens} token 以内，只输出摘要正文。"
)


class ConversationSummarizer:
    """Summarize finished conversations into ``kind=summary`` memories.

    Idempotent per source run: the same ``run_id`` never writes twice. A new
    summary supersedes the previous one through the memory revision chain
    (old record becomes inactive). LLM failures are swallowed: they must not
    fail the completed agent run; a ``summary_failed`` run event is recorded
    and the next run retries.
    """

    def __init__(
        self,
        repository: ApplicationRepository,
        knowledge: KnowledgeRepository,
        gateway: LLMGateway,
        settings: Settings,
        governance: MemoryGovernanceService | None = None,
    ) -> None:
        self._repository = repository
        self._knowledge = knowledge
        self._gateway = gateway
        self._settings = settings
        # M23: 治理 Gate（摘要属 generated_content，写 conversation_summary）。
        self._governance = governance

    async def summarize(self, *, case_id: str, run_id: str) -> None:
        if not self._gateway.configured:
            return
        memories = await self._knowledge.list_memories(case_id)
        if any(
            m.kind == "summary" and m.source_id == run_id and m.active
            for m in memories
        ):
            return

        turns = await self._repository.list_turns(case_id)
        transcript = "\n".join(
            f"[{'用户' if t.role == 'user' else '助手'}] {t.content}" for t in turns
        )
        if not transcript.strip():
            return

        try:
            response = await self._gateway.complete(
                messages=[
                    LLMMessage(
                        role="system",
                        content=_SUMMARY_PROMPT.format(
                            max_tokens=self._settings.context_summary_max_tokens
                        ),
                    ),
                    LLMMessage(role="user", content=transcript),
                ],
                tools=[],
                route=ModelRoute.FAST,
            )
        except Exception:
            logger.exception("conversation summary failed for run %s", run_id)
            await self._repository.add_run_event(
                run_id,
                {
                    "event_type": "summary_failed",
                    "agent": "coordinator",
                    "status": "failed",
                    "error": "conversation_summary_llm_failed",
                },
            )
            return

        content = (response.message.content or "").strip()
        if not content:
            return

        previous = sorted(
            (m for m in memories if m.kind == "summary"),
            key=lambda m: m.updated_at,
        )
        request = CreateMemoryRequest(
            scope="case",
            kind="summary",
            content=content[: self._settings.context_summary_max_tokens * 4],
            source_type="conversation",
            source_id=run_id,
            importance=0.6,
            confidence=1,
            supersedes_id=previous[-1].id if previous else None,
            # M23: 摘要来源 turn 引用 + "摘要非事实"标签（模型生成，不得当事实）。
            metadata={
                "source_turns": [t.id for t in turns],
                "summary_not_fact": summary_tag(MEMORY_TYPE_CONVERSATION_SUMMARY),
            },
        )
        if self._governance is not None:
            await self._governance.persist_governed(
                case_id=case_id,
                request=request,
                memory_type=MEMORY_TYPE_CONVERSATION_SUMMARY,
                trust_level=TRUST_GENERATED_CONTENT,
                actor="conversation_summarizer",
            )
        else:
            await self._knowledge.create_memory(case_id, request)
