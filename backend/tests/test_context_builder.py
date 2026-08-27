"""ContextBuilder: constraint-first assembly, windowing, budget, degradation."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.application.context_builder import ContextBuilder
from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.models import (
    ArtifactRecord,
    ConversationTurnRecord,
)
from app.infrastructure.llm import LLMMessage
from app.schemas.cases import CreateCaseRequest
from app.schemas.knowledge import CreateMemoryRequest


async def _seed(
    database: Database,
    *,
    with_summary: bool = False,
) -> tuple[ApplicationRepository, KnowledgeRepository, object, object]:
    repository = ApplicationRepository(database)
    knowledge = KnowledgeRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="新能源汽车争议", platforms=["weibo"])
    )
    await knowledge.create_memory(
        case.id,
        CreateMemoryRequest(
            kind="constraint",
            content="用户确认只分析微博平台，不扩展到其他平台。",
            source_type="user_constraint",
            source_id="turn-1",
            importance=1,
        ),
    )
    await knowledge.create_memory(
        case.id,
        CreateMemoryRequest(
            kind="fact",
            content="官方公告显示已启动主动召回。",
            source_type="social_post",
            source_id="p1",
            importance=0.9,
        ),
    )
    await knowledge.create_memory(
        case.id,
        CreateMemoryRequest(
            kind="fact",
            content="某网友猜测召回原因。",
            source_type="social_post",
            source_id="p2",
            importance=0.2,
        ),
    )
    if with_summary:
        await knowledge.create_memory(
            case.id,
            CreateMemoryRequest(
                kind="summary",
                content="早期对话摘要：用户确认研究范围。",
                source_type="conversation",
                source_id="run-early",
                importance=0.6,
            ),
        )
    async with database.session_factory() as session:
        session.add(
            ArtifactRecord(
                case_id=case.id,
                kind="fact_check",
                title="召回范围核查卡",
                version=1,
                data={"verdict": "supported"},
            )
        )
        for _index, (role, content) in enumerate(
            [
                ("user", "第1轮 用户提问"),
                ("assistant", "第1轮 助手回答"),
                ("user", "第2轮 用户追问"),
                ("assistant", "第2轮 助手回答"),
                ("user", "第3轮 用户补充"),
                ("assistant", "第3轮 助手回答"),
            ]
        ):
            session.add(
                ConversationTurnRecord(
                    case_id=case.id,
                    role=role,
                    content=content,
                )
            )
        await session.commit()
    return repository, knowledge, case, case


def _history() -> list[LLMMessage]:
    return [
        LLMMessage(role="user", content="第1轮 用户提问"),
        LLMMessage(role="assistant", content="第1轮 助手回答"),
        LLMMessage(role="user", content="第2轮 用户追问"),
        LLMMessage(role="assistant", content="第2轮 助手回答"),
        LLMMessage(role="user", content="第3轮 用户补充"),
        LLMMessage(role="assistant", content="第3轮 助手回答"),
    ]


def test_build_injects_constraints_memory_and_artifacts(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'ctx.db'}")
    asyncio.run(database.create_schema())

    async def run() -> None:
        repository, knowledge, case, _ = await _seed(database)
        builder = ContextBuilder(repository, knowledge, Settings())
        built = await builder.build(
            case=case,
            run=object(),
            history=_history(),
            skill_catalog="可用 Skill：opinion-research",
        )
        assert "只分析微博平台" in built.system_context
        assert "已启动主动召回" in built.system_context
        assert "召回范围核查卡" in built.system_context
        assert built.stats["constraint_count"] == 1
        assert built.stats["memory_count"] == 1  # importance >= 0.7 only

    asyncio.run(run())
    asyncio.run(database.dispose())


def test_history_window_and_summary(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'ctx.db'}")
    asyncio.run(database.create_schema())

    async def run() -> None:
        repository, knowledge, case, _ = await _seed(database, with_summary=True)
        builder = ContextBuilder(
            repository,
            knowledge,
            Settings(context_history_turns=2),
        )
        built = await builder.build(
            case=case,
            run=object(),
            history=_history(),
            skill_catalog="",
        )
        assert len(built.history_window) == 2
        assert built.history_window[-1].content == "第3轮 助手回答"
        assert "早期对话摘要" in built.system_context
        assert built.stats["summary_used"] is True

    asyncio.run(run())
    asyncio.run(database.dispose())


def test_token_budget_truncates_low_priority_but_keeps_constraints(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'ctx.db'}")
    asyncio.run(database.create_schema())

    async def run() -> None:
        repository, knowledge, case, _ = await _seed(database)
        builder = ContextBuilder(
            repository,
            knowledge,
            Settings(context_token_budget=40),
        )
        built = await builder.build(
            case=case,
            run=object(),
            history=_history(),
            skill_catalog="",
        )
        # 约束永不裁剪
        assert "只分析微博平台" in built.system_context
        # 预算过小：高重要性 memory / artifact 索引被裁掉
        assert "已启动主动召回" not in built.system_context
        assert "召回范围核查卡" not in built.system_context

    asyncio.run(run())
    asyncio.run(database.dispose())


def test_degradation_on_lookup_failure(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'ctx.db'}")
    asyncio.run(database.create_schema())

    async def run() -> None:
        repository, _, case, _ = await _seed(database)

        class BrokenKnowledge:
            async def list_memories(self, case_id: str):  # noqa: ARG002
                raise RuntimeError("db down")

        builder = ContextBuilder(repository, BrokenKnowledge(), Settings())
        built = await builder.build(
            case=case,
            run=object(),
            history=_history(),
            skill_catalog="",
        )
        assert built.stats.get("degraded") is True
        assert built.history_window == _history()  # 降级保留全量历史

    asyncio.run(run())
    asyncio.run(database.dispose())
