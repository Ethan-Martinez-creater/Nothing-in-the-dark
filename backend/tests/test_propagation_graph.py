"""C7: Propagation graph DTO endpoint 测试。

- nodes 按 post 去重聚合（同 post 多 role → roles 列表 + 最高分主 role）
- node label/excerpt/platform 来自 SourcePostRecord join
- edges 复用既有字段（含 human_confirmed）
- 跨 case 隔离：图只含当前 case 数据；未知 case 404
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.database.social_repository import SocialRepository
from app.main import create_app
from app.schemas.cases import CreateCaseRequest


async def _seed_graph(database: Database) -> tuple[str, str, dict[str, str]]:
    """返回 (case_id, other_case_id, {label: post_id})。"""
    await database.create_schema()
    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="传播图案例", platforms=["weibo"])
    )
    other = await repository.create_case(
        CreateCaseRequest(topic="其他案例", platforms=["weibo"])
    )
    social = SocialRepository(database)
    await social.persist_batch(
        case_id=case.id,
        posts=[
            {
                "platform": "weibo",
                "native_id": "n1",
                "title": "首发爆料",
                "content": "某事件首发帖子内容",
                "author": "账号A",
                "published_at": datetime(2026, 8, 1, tzinfo=UTC).isoformat(),
                "engagement": {"likes": 100},
            },
            {
                "platform": "weibo",
                "native_id": "n2",
                "title": "",
                "content": "转发评论内容",
                "author": "账号B",
                "published_at": datetime(2026, 8, 2, tzinfo=UTC).isoformat(),
                "engagement": {},
            },
        ],
    )
    # 拿真实生成的 post id
    all_posts = await social.list_posts_by_case(case.id)
    post_ids = {post.native_id: post.id for post in all_posts}

    # 同一 post 两个 role：source（高分）+ burst（低分）→ 图中一个节点
    await repository.create_propagation_node(
        case_id=case.id, post_id=post_ids["n1"], role="burst", score=0.6
    )
    await repository.create_propagation_node(
        case_id=case.id, post_id=post_ids["n1"], role="source", score=0.9
    )
    await repository.create_propagation_node(
        case_id=case.id, post_id=post_ids["n2"], role="hub", score=0.5
    )
    await repository.create_propagation_edge(
        case_id=case.id,
        source_post_id=post_ids["n1"],
        target_post_id=post_ids["n2"],
        relation="copy_spread",
        confidence=0.83,
        feature_scores={"text_sim": 0.83},
        evidence_ids=["ev-1"],
        algorithm_version="prop-v2",
    )
    # 其他 case 的 node 不得泄漏主 case 图（其 post 不存在）
    await repository.create_propagation_node(
        case_id=other.id, post_id=post_ids["n1"], role="source", score=0.1
    )
    return case.id, other.id, {"src": post_ids["n1"], "dst": post_ids["n2"]}


async def test_propagation_graph_service_aggregation(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pg1.db'}")
    case_id, _, ids = await _seed_graph(database)
    repository = ApplicationRepository(database)
    nodes, edges, posts = await repository.list_propagation_graph(case_id)

    # node 按 post 去重：src（2 roles）+ dst
    assert {node.post_id for node in nodes} == {ids["src"], ids["dst"]}
    assert len(edges) == 1
    assert edges[0].relation == "copy_spread"
    assert edges[0].human_confirmed is False
    # join post 元数据可用
    assert posts[ids["src"]].platform == "weibo"
    assert posts[ids["src"]].author_name == "账号A"


def test_propagation_graph_api(tmp_path: Path) -> None:
    import asyncio

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pg2.db'}")
    case_id, other_id, ids = asyncio.run(_seed_graph(database))
    asyncio.run(database.dispose())

    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'pg2.db'}", demo_mode=True)
    )
    with TestClient(app) as client:
        body = client.get(f"/api/v1/cases/{case_id}/propagation-graph").json()
        # nodes 按 post 去重，主 role 取最高分
        by_post = {node["post_id"]: node for node in body["nodes"]}
        assert set(by_post) == {ids["src"], ids["dst"]}
        src = by_post[ids["src"]]
        assert src["role"] == "source"
        assert src["roles"] == ["source", "burst"]
        assert src["score"] == 0.9
        assert src["label"] == "首发爆料"
        assert "首发爆料" in src["excerpt"]
        assert src["platform"] == "weibo"
        assert src["author_name"] == "账号A"
        assert src["published_at"] is not None
        dst = by_post[ids["dst"]]
        assert dst["label"] == "账号B"  # 无标题回退 author_name（再回退 post_id）
        # edges 复用既有字段
        assert len(body["edges"]) == 1
        edge = body["edges"][0]
        assert edge["source_post_id"] == ids["src"]
        assert edge["target_post_id"] == ids["dst"]
        assert edge["confidence"] == 0.83
        assert edge["evidence_ids"] == ["ev-1"]
        assert edge["algorithm_version"] == "prop-v2"

        # 跨 case 隔离：其他 case 图不含主 case 的 edges；node 无 post 元数据
        other_body = client.get(f"/api/v1/cases/{other_id}/propagation-graph").json()
        assert other_body["edges"] == []
        assert all(node["platform"] == "unknown" for node in other_body["nodes"])

        # 未知 case → 404
        missing = client.get("/api/v1/cases/no-such-case/propagation-graph")
        assert missing.status_code == 404
