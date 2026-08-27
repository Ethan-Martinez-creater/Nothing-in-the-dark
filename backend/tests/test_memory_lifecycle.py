import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.application.agent_service import AgentRunService
from app.application.context_builder import ContextBuilder
from app.application.graph_worker import GraphWorker
from app.application.memory_extraction import (
    CaseMemoryExtractor,
    MemoryExtractionService,
)
from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.harness.skills import SkillRegistry
from app.harness.tool_factory import build_tool_registry
from app.infrastructure.crawler.demo import DemoCrawlerAdapter
from app.infrastructure.database import Database
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.embeddings import EmbeddingWorkerClient
from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse, ModelRoute
from app.main import create_app
from app.schemas.cases import CreateCaseRequest
from app.schemas.knowledge import CreateMemoryRequest
from app.services.memory_extraction import (
    extract_memory_candidates,
    find_related,
    find_similar,
    should_decay,
    text_similarity,
)

# ---------- 服务层 ----------

def test_extract_candidates_classifies_patterns() -> None:
    candidates = extract_memory_candidates(
        "记住：这个案例优先关注微博平台。请用中文回复。"
        "之前说的结论作废，应该是相反的。普通句子不提取。"
    )

    kinds = [candidate.kind for candidate in candidates]
    assert "constraint" in kinds
    assert "correction" in kinds
    # 无模式句不应被提取
    assert all("普通句子" not in candidate.content for candidate in candidates)


def test_extract_importance_ordering() -> None:
    candidates = extract_memory_candidates(
        "应该是 B 账号是源头（纠正）。以后每次都先查证据（指令）。"
    )
    by_kind = {candidate.kind: candidate.importance for candidate in candidates}

    assert by_kind["correction"] > by_kind["constraint"]


def test_text_similarity_and_find_similar() -> None:
    assert text_similarity("同一句话", "同一句话") == 1.0
    assert text_similarity("完全无关内容abc", "另一段完全不同的话") < 0.5

    match = find_similar(
        ["请记住这个案例关注微博", "其他无关记忆"],
        "记住这个案例关注微博平台",
        threshold=0.6,
    )
    assert match is not None
    assert match[0] == 0

    assert find_similar(["无关记忆"], "完全不同的话题内容", threshold=0.9) is None


def test_find_related_matches_related_topic_despite_low_text_similarity() -> None:
    old = "记住：源头账号是 A。"
    correction = "不对，源头账号应该是 B，更正之前的结论。"

    # 整句相似度低于去重阈值，但主题相关
    assert find_similar([old], correction, threshold=0.6) is None
    match = find_related([old], correction, threshold=0.2)
    assert match is not None
    assert match[0] == 0

    # 无关内容不应命中
    assert find_related(["请记住优先关注微博平台"], correction, threshold=0.2) is None


def test_should_decay_rules() -> None:
    now = datetime.now(UTC)
    old = now - timedelta(days=200)
    recent = now - timedelta(days=10)

    assert should_decay(
        old, 0.3, now=now, ttl_days=180, min_importance=0.4
    )
    # 重要性达标不衰减
    assert not should_decay(
        old, 0.6, now=now, ttl_days=180, min_importance=0.4
    )
    # 时间不足不衰减
    assert not should_decay(
        recent, 0.3, now=now, ttl_days=180, min_importance=0.4
    )


# ---------- 端点集成 ----------

def _create_case(client: TestClient) -> str:
    return client.post(
        "/api/v1/cases",
        json={"topic": "Memory 生命周期", "platforms": ["weibo"]},
    ).json()["id"]


def test_extract_endpoint_persists_and_dedups(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case_id = _create_case(client)
        first = client.post(
            f"/api/v1/cases/{case_id}/memories/extract",
            json={"text": "请记住：优先关注微博平台。"},
        )
        second = client.post(
            f"/api/v1/cases/{case_id}/memories/extract",
            json={"text": "请记住：优先关注微博平台。"},
        )
        third = client.post(
            f"/api/v1/cases/{case_id}/memories/extract",
            json={"text": "普通句子没有记忆指令。"},
        )

    assert first.status_code == 201
    assert len(first.json()) == 1
    created = first.json()[0]
    assert created["kind"] == "constraint"
    assert created["importance"] == 0.85
    assert created["source_type"] == "conversation"
    assert created["active"] is True
    # 重复提取去重（同文本相似 → 跳过）
    assert len(second.json()) == 0
    # 无模式文本不产生候选
    assert third.json() == []


def test_extract_correction_supersedes_old_value(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'correct.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case_id = _create_case(client)
        client.post(
            f"/api/v1/cases/{case_id}/memories/extract",
            json={"text": "记住：源头账号是 A。"},
        )
        correction = client.post(
            f"/api/v1/cases/{case_id}/memories/extract",
            json={"text": "不对，源头账号应该是 B，更正之前的结论。"},
        )
        memories = client.get(
            f"/api/v1/cases/{case_id}/memories",
            params={"include_inactive": True},
        )

    assert len(correction.json()) == 1
    new_memory = correction.json()[0]
    assert new_memory["kind"] == "correction"
    assert new_memory["supersedes_id"] is not None
    records = memories.json()
    active = [record for record in records if record["active"]]
    assert len(active) == 1
    assert active[0]["content"] == new_memory["content"]
    # 修订轨迹保留：旧值仍在（inactive）
    inactive = [record for record in records if not record["active"]]
    assert len(inactive) == 1


def test_decay_endpoint_deactivates_stale_memories(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'decay.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case_id = _create_case(client)
        # 手工造一条低重要性、很久未更新的 Memory
        repo = client.app.state.container.knowledge

        async def _seed() -> None:
            record = await repo.create_memory(
                case_id,
                CreateMemoryRequest(
                    scope="case",
                    kind="fact",
                    content="过时的低重要性记忆",
                    source_type="seed",
                    source_id="seed-1",
                    importance=0.2,
                ),
            )
            # updated_at 需在 session 内修改才会持久化
            async with repo._database.session_factory() as session:
                persisted = await session.get(type(record), record.id)
                persisted.updated_at = datetime.now(UTC) - timedelta(days=400)
                await session.commit()

        asyncio.run(_seed())
        result = client.post(
            f"/api/v1/cases/{case_id}/memories/decay",
            json={"ttl_days": 180, "min_importance": 0.4},
        )
        memories = client.get(
            f"/api/v1/cases/{case_id}/memories",
            params={"include_inactive": True},
        )

    assert result.status_code == 200
    assert result.json()["deactivated"] == 1
    records = memories.json()
    assert all(not record["active"] for record in records)


# ---------- Case Memory 自动更新（run 收尾自动提取） ----------

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


def test_case_memory_extractor_auto_extract_idempotent(tmp_path: Path) -> None:
    """Run-end extraction persists candidates, is idempotent per run and
    dedups the same transcript across runs."""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'auto.db'}")

    async def run() -> None:
        await database.create_schema()
        repository = ApplicationRepository(database)
        knowledge = KnowledgeRepository(database)
        embeddings = EmbeddingWorkerClient(
            "", dimensions=1024, timeout_seconds=1
        )
        extractor = CaseMemoryExtractor(
            repository,
            knowledge,
            MemoryExtractionService(knowledge, embeddings),
        )
        case = await repository.create_case(
            CreateCaseRequest(topic="自动提取", platforms=["weibo"])
        )
        await repository.add_turn(
            case.id, role="user", content="请记住：优先关注微博平台。"
        )

        await extractor.extract(case_id=case.id, run_id="run-1")
        memories = await knowledge.list_memories(case.id)
        extracted = [m for m in memories if m.metadata_json.get("extracted")]
        assert len(extracted) == 1
        assert extracted[0].kind == "constraint"
        assert extracted[0].source_id == "auto:run-1"
        assert extracted[0].importance == 0.85

        # 同一 run 不重复提取
        await extractor.extract(case_id=case.id, run_id="run-1")
        assert len(await knowledge.list_memories(case.id)) == 1

        # 新 run 相同文本：相似去重跳过
        await extractor.extract(case_id=case.id, run_id="run-2")
        assert len(await knowledge.list_memories(case.id)) == 1

        # 无模式文本不产生候选
        await repository.add_turn(case.id, role="user", content="今天天气不错。")
        await extractor.extract(case_id=case.id, run_id="run-3")
        assert len(await knowledge.list_memories(case.id)) == 1

    asyncio.run(run())
    asyncio.run(database.dispose())


def test_worker_auto_extracts_memory_after_run(tmp_path: Path) -> None:
    """GraphWorker wiring: a completed coordinator run auto-extracts
    conversation-end memory candidates."""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'worker_auto.db'}")

    async def run() -> None:
        await database.create_schema()
        repository = ApplicationRepository(database)
        knowledge = KnowledgeRepository(database)
        social = SocialRepository(database)
        embeddings = EmbeddingWorkerClient(
            "", dimensions=1024, timeout_seconds=1
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
        settings = Settings()
        worker = GraphWorker(
            repository,
            DoneGateway(),
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
            extractor=CaseMemoryExtractor(
                repository,
                knowledge,
                MemoryExtractionService(knowledge, embeddings),
            ),
        )
        case = await repository.create_case(
            CreateCaseRequest(topic="自动提取接线", platforms=["weibo"])
        )
        service = AgentRunService(repository, worker)
        run_record = await service.start(
            case_id=case.id,
            content="请记住：优先关注微博平台。帮我分析这个案例",
            approve_crawl=False,
        )
        await worker.tick(wait=True)
        current = await repository.get_agent_run(run_record.id)
        assert current.status == "completed"
        memories = await knowledge.list_memories(case.id)
        extracted = [m for m in memories if m.metadata_json.get("extracted")]
        assert len(extracted) == 1
        assert extracted[0].kind == "constraint"
        assert extracted[0].source_id == f"auto:{run_record.id}"

    asyncio.run(run())
    asyncio.run(database.dispose())


# ---------- 长期领域 Memory 权限隔离 ----------

def test_domain_memory_isolated_from_case(tmp_path: Path) -> None:
    """Domain memories are managed globally and never leak into case memory
    listing or case RAG search."""
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'domain.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case_id = _create_case(client)
        created = client.post(
            "/api/v1/memories/domain",
            json={
                "scope": "case",  # 服务端强制转为 domain
                "kind": "constraint",
                "content": "长期领域记忆：所有案例优先使用中文",
                "source_type": "admin",
                "source_id": "seed-1",
                "importance": 0.9,
            },
        )
        listed = client.get("/api/v1/memories/domain")
        case_memories = client.get(f"/api/v1/cases/{case_id}/memories")
        search = client.post(
            f"/api/v1/cases/{case_id}/memory/search",
            json={"query": "中文"},
        )

    assert created.status_code == 201
    assert created.json()["scope"] == "domain"
    assert created.json()["case_id"] is None
    assert len(listed.json()) == 1
    # 不泄漏进 case 视图与检索
    assert case_memories.json() == []
    assert search.json() == []
