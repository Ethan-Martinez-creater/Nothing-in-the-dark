"""ConversationSummarizer: idempotency, revision chain, failure tolerance."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.application.conversation_summary import ConversationSummarizer
from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.llm import (
    LLMGateway,
    LLMMessage,
    LLMResponse,
    ModelRoute,
)
from app.schemas.cases import CreateCaseRequest


class FakeSummaryGateway(LLMGateway):
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    @property
    def configured(self) -> bool:
        return True

    async def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        route: ModelRoute,
        temperature: float = 0,
    ) -> LLMResponse:
        if self._fail:
            raise RuntimeError("llm down")
        return LLMResponse(
            message=LLMMessage(role="assistant", content="摘要：用户确认研究范围。"),
            model="fake-model",
        )


async def _seed(database: Database) -> tuple[ApplicationRepository, str]:
    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="新能源汽车争议", platforms=["weibo"])
    )
    await repository.add_turn(case.id, role="user", content="帮我分析这个案例")
    await repository.add_turn(case.id, role="assistant", content="好的，开始分析")
    return repository, case.id


def test_summarize_writes_summary_memory(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'sum.db'}")
    asyncio.run(database.create_schema())

    async def run() -> None:
        repository, case_id = await _seed(database)
        knowledge = KnowledgeRepository(database)
        summarizer = ConversationSummarizer(
            repository,
            knowledge,
            FakeSummaryGateway(),
            Settings(),
        )
        run_record = await repository.create_agent_run(
            case_id=case_id,
            turn_id=None,
            objective="帮我分析这个案例",
        )
        await summarizer.summarize(case_id=case_id, run_id=run_record.id)
        memories = await knowledge.list_memories(case_id)
        summaries = [m for m in memories if m.kind == "summary"]
        assert len(summaries) == 1
        assert summaries[0].source_id == run_record.id
        assert "研究范围" in summaries[0].content

    asyncio.run(run())
    asyncio.run(database.dispose())


def test_summarize_is_idempotent_per_run(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'sum.db'}")
    asyncio.run(database.create_schema())

    async def run() -> None:
        repository, case_id = await _seed(database)
        knowledge = KnowledgeRepository(database)
        summarizer = ConversationSummarizer(
            repository,
            knowledge,
            FakeSummaryGateway(),
            Settings(),
        )
        run_record = await repository.create_agent_run(
            case_id=case_id, turn_id=None, objective="x"
        )
        await summarizer.summarize(case_id=case_id, run_id=run_record.id)
        await summarizer.summarize(case_id=case_id, run_id=run_record.id)
        memories = await knowledge.list_memories(case_id)
        assert len([m for m in memories if m.kind == "summary"]) == 1

    asyncio.run(run())
    asyncio.run(database.dispose())


def test_new_summary_supersedes_previous(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'sum.db'}")
    asyncio.run(database.create_schema())

    async def run() -> None:
        repository, case_id = await _seed(database)
        knowledge = KnowledgeRepository(database)
        summarizer = ConversationSummarizer(
            repository,
            knowledge,
            FakeSummaryGateway(),
            Settings(),
        )
        first = await repository.create_agent_run(
            case_id=case_id, turn_id=None, objective="a"
        )
        second = await repository.create_agent_run(
            case_id=case_id, turn_id=None, objective="b"
        )
        await summarizer.summarize(case_id=case_id, run_id=first.id)
        await summarizer.summarize(case_id=case_id, run_id=second.id)
        memories = await knowledge.list_memories(case_id, include_inactive=True)
        summaries = [m for m in memories if m.kind == "summary"]
        active = [m for m in summaries if m.active]
        assert len(active) == 1
        assert active[0].source_id == second.id

    asyncio.run(run())
    asyncio.run(database.dispose())


def test_llm_failure_does_not_raise(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'sum.db'}")
    asyncio.run(database.create_schema())

    async def run() -> None:
        repository, case_id = await _seed(database)
        knowledge = KnowledgeRepository(database)
        summarizer = ConversationSummarizer(
            repository,
            knowledge,
            FakeSummaryGateway(fail=True),
            Settings(),
        )
        run_record = await repository.create_agent_run(
            case_id=case_id, turn_id=None, objective="x"
        )
        await summarizer.summarize(case_id=case_id, run_id=run_record.id)
        memories = await knowledge.list_memories(case_id)
        assert not [m for m in memories if m.kind == "summary"]
        events = await repository.list_run_events(run_record.id)
        assert any(e.event_type == "summary_failed" for e in events)

    asyncio.run(run())
    asyncio.run(database.dispose())
