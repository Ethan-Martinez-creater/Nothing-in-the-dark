"""GraphWorker integration: context_built event and summary memory."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from app.application.agent_service import AgentRunService
from app.application.context_builder import ContextBuilder
from app.application.conversation_summary import ConversationSummarizer
from app.application.graph_worker import GraphWorker
from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.harness.skills import SkillRegistry
from app.harness.tool_factory import build_tool_registry
from app.infrastructure.crawler.demo import DemoCrawlerAdapter
from app.infrastructure.database import Database
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.embeddings import EmbeddingWorkerClient
from app.infrastructure.llm import (
    LLMGateway,
    LLMMessage,
    LLMResponse,
    ModelRoute,
)
from app.schemas.cases import CreateCaseRequest
from app.schemas.knowledge import CreateMemoryRequest


class DoneGateway(LLMGateway):
    """Immediately answers without tool calls."""

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
        return LLMResponse(
            message=LLMMessage(role="assistant", content="完成。"),
            model="fake-model",
        )


class SummaryGateway(DoneGateway):
    """Answers for the coordinator, then summarizes the conversation."""

    def __init__(self) -> None:
        self._calls = 0

    async def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        route: ModelRoute,
        temperature: float = 0,
    ) -> LLMResponse:
        self._calls += 1
        system = messages[0].content or ""
        if "对话摘要助手" in system:
            return LLMResponse(
                message=LLMMessage(
                    role="assistant",
                    content="摘要：用户要求分析新能源汽车争议。",
                ),
                model="fake-model",
            )
        return LLMResponse(
            message=LLMMessage(role="assistant", content="完成。"),
            model="fake-model",
        )


def test_run_emits_context_built_and_writes_summary(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'ctx_run.db'}")

    async def run() -> None:
        await database.create_schema()
        repository = ApplicationRepository(database)
        knowledge = KnowledgeRepository(database)
        social = SocialRepository(database)
        embeddings = EmbeddingWorkerClient(
            "http://localhost:1", dimensions=1024, timeout_seconds=1
        )
        skills = SkillRegistry()
        tools = build_tool_registry(
            DemoCrawlerAdapter(),
            skills,
            knowledge,
            embeddings,
            social,
            repository,
        )
        gateway = SummaryGateway()
        settings = Settings()
        worker = GraphWorker(
            repository,
            gateway,
            tools,
            skills,
            worker_id="test",
            poll_interval_seconds=0.01,
            lease_seconds=300,
            max_turns=8,
            max_tool_calls=16,
            max_cost=5,
            checkpointer=MemorySaver(),
            context_builder=ContextBuilder(repository, knowledge, settings),
            summarizer=ConversationSummarizer(
                repository, knowledge, gateway, settings
            ),
        )
        case = await repository.create_case(
            CreateCaseRequest(topic="新能源汽车争议", platforms=["weibo"])
        )
        await repository.add_turn(case.id, role="user", content="帮我分析")
        await repository.add_turn(case.id, role="assistant", content="好的")
        await knowledge.create_memory(
            case.id,
            CreateMemoryRequest(
                kind="constraint",
                content="只分析微博平台",
                source_type="user_constraint",
                source_id="turn-1",
                importance=1,
            ),
        )
        service = AgentRunService(repository, worker)
        run_record = await service.start(
            case_id=case.id,
            content="帮我分析这个案例",
            approve_crawl=False,
        )
        await worker.tick(wait=True)
        current = await repository.get_agent_run(run_record.id)
        assert current.status == "completed"
        events = await repository.list_run_events(run_record.id)
        assert any(e.event_type == "context_built" for e in events)
        memories = await knowledge.list_memories(case.id)
        summaries = [m for m in memories if m.kind == "summary"]
        assert len(summaries) == 1
        assert "新能源汽车" in summaries[0].content

    asyncio.run(run())
    asyncio.run(database.dispose())
