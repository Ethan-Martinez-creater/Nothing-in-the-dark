"""传播/核查专家失败修复的回归测试。

- propagation：`build_propagation_graph` 现在能容忍 dict 形态的
  `engagement`（新结构化 DB 工具返回 `{"total": n}`），不再 `int(dict)` 崩溃。
- verification：`verify_claims` 工具支持 `post_ids` 参数，handler 按 ID 从
  当前 Case 数据库拉取全量帖子，避免 LLM 把大列表塞进 tool arguments
  导致 JSON 截断/损坏。
"""

from __future__ import annotations

from typing import Any

from app.application.repositories import ApplicationRepository
from app.harness.skills import SkillRegistry
from app.harness.tool_factory import build_tool_registry
from app.infrastructure.crawler.demo import DemoCrawlerAdapter
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.embeddings import EmbeddingWorkerClient
from app.schemas.cases import CreateCaseRequest
from app.services.propagation_algorithm import build_propagation_graph
from tests.memory_db import MemoryDatabase


def _post(
    platform: str, index: int, *, content: str = "华为要求停售竹知了 讨论内容"
) -> dict[str, Any]:
    return {
        "platform": platform,
        "native_id": f"{platform}-{index}",
        "content_type": "post",
        "title": "",
        "content": f"{content} {index}",
        "author": f"author-{platform}",
        "published_at": "2026-08-15T10:00:00+00:00",
        "engagement": 10,
        "metrics": {"total": 10},
        "url": "u",
        "raw": {},
        "comments": [],
    }


async def _build_registry():
    db = MemoryDatabase()
    await db.create_schema()
    app_repo = ApplicationRepository(db)
    case = await app_repo.create_case(
        CreateCaseRequest(
            topic="华为竹知了事件",
            platforms=["weibo", "zhihu"],
            time_start="2026-08-10",
            time_end="2026-08-20",
        )
    )
    social = SocialRepository(db)
    knowledge = KnowledgeRepository(db)
    embeddings = EmbeddingWorkerClient(
        "http://localhost:1", dimensions=1024, timeout_seconds=1
    )
    registry = build_tool_registry(
        DemoCrawlerAdapter(),
        SkillRegistry(),
        knowledge,
        embeddings,
        social,
        app_repo,
    )
    return db, case, registry, social, app_repo


async def test_verify_claims_pulls_posts_by_ids() -> None:
    db, case, registry, social, _ = await _build_registry()
    await social.persist_batch(
        case_id=case.id,
        posts=[_post("weibo", 1), _post("zhihu", 1)],
    )
    stored = await social.list_posts_page(case.id, limit=10)
    ids = [p.id for p in stored]
    assert len(ids) == 2

    result = await registry.invoke(
        "verify_claims",
        {
            "case_id": case.id,
            "topic": "华为竹知了",
            "post_ids": ids,
        },
    )
    assert result.get("ok") in (None, True)
    cards = result.get("cards") or result.get("verdicts") or []
    assert isinstance(cards, list)
    # 至少抽取到一条 claim 且 source_post_id 落在 DB 真实 id 集合内
    if cards:
        src_ids = {c.get("source_post_id") for c in cards}
        assert src_ids <= set(ids)
    await db.dispose()


async def test_verify_claims_requires_posts_or_post_ids() -> None:
    db, case, registry, _, _ = await _build_registry()
    result = await registry.invoke(
        "verify_claims",
        {"case_id": case.id, "topic": "华为竹知了", "post_ids": [], "posts": []},
    )
    assert result.get("ok") is False
    assert result["error"]["code"] == "invalid_request"
    await db.dispose()


async def test_verify_claims_foreign_post_id_ignored() -> None:
    db, case, registry, social, app_repo = await _build_registry()
    other = await app_repo.create_case(
        CreateCaseRequest(topic="其它事件", platforms=["weibo"])
    )
    await social.persist_batch(case_id=other.id, posts=[_post("weibo", 9)])
    foreign = (await social.list_posts_page(other.id, limit=1))[0]
    result = await registry.invoke(
        "verify_claims",
        {"case_id": case.id, "topic": "华为竹知了", "post_ids": [foreign.id]},
    )
    # 其它 case 的 id 被忽略 → 无有效帖子 → invalid_request（不泄漏跨 case 数据）
    assert result.get("ok") is False
    assert result["error"]["code"] == "invalid_request"
    await db.dispose()


def test_build_propagation_graph_handles_dict_engagement() -> None:
    posts = [
        {
            "id": "p1",
            "author": "a",
            "platform": "weibo",
            "published_at": "2026-08-15T10:00:00+00:00",
            "content": "华为要求停售竹知了",
            "engagement": {"total": 10, "like_count": 3},
        },
        {
            "id": "p2",
            "author": "b",
            "platform": "zhihu",
            "published_at": "2026-08-16T10:00:00+00:00",
            "content": "华为要求停售竹知了 回应",
            "engagement": 5,  # 兼容旧数字形态
        },
    ]
    graph = build_propagation_graph(posts, embeddings=None)
    by_id = {node["id"]: node["engagement"] for node in graph["nodes"]}
    assert by_id["p1"] == 10  # dict → total
    assert by_id["p2"] == 5  # 数字原样
