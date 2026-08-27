"""P1-2.2: crawl/analysis results land in domain tables, not only JSON."""

from __future__ import annotations

from pathlib import Path

from app.application.domain_ingest import (
    artifact_references,
    ingest_accounts_from_posts,
    ingest_after_crawl,
    ingest_entities_from_posts,
    ingest_propagation_nodes,
    persist_run_cost_summary,
)
from app.application.repositories import ApplicationRepository
from app.infrastructure.database import Database
from app.infrastructure.database.social_repository import SocialRepository
from app.schemas.cases import CreateCaseRequest


def _posts() -> list[dict]:
    return [
        {
            "id": "weibo-1",
            "native_id": "weibo-1",
            "platform": "weibo",
            "author": "观察员甲",
            "author_id": "u-1",
            "content": "官方回应称2026年8月事故伤亡数据失实",
            "published_at": "2026-08-01T09:00:00+00:00",
            "image_url": "https://cdn.example.com/a.jpg",
            "follower_count": 12,
        },
        {
            "id": "bili-1",
            "native_id": "bili-1",
            "platform": "bilibili",
            "author": "记录与核查",
            "author_id": "u-2",
            "content": "时间线梳理 ¥100 元赔偿传闻",
            "published_at": "2026-08-01T10:00:00+00:00",
        },
    ]


async def _setup(tmp_path: Path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'ingest.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="入库", platforms=["weibo", "bilibili"])
    )
    return database, repository, social, case.id


async def test_ingest_accounts_and_entities(tmp_path: Path) -> None:
    database, repository, social, case_id = await _setup(tmp_path)
    try:
        await social.persist_batch(case_id=case_id, posts=_posts())
        accounts = await ingest_accounts_from_posts(repository, case_id, _posts())
        entities = await ingest_entities_from_posts(repository, case_id, _posts())
        assert accounts["accounts"] == 2
        assert entities["entities"] >= 1
        stored = await repository.list_accounts(case_id=case_id)
        names = {item.name for item in stored}
        assert "观察员甲" in names
        tokens = {item.name for item in await repository.list_entities(case_id)}
        assert any("2026" in token or "100" in token or "元" in token for token in tokens)
    finally:
        await database.dispose()


async def test_ingest_after_crawl_includes_media(tmp_path: Path) -> None:
    database, repository, social, case_id = await _setup(tmp_path)
    try:
        await social.persist_batch(case_id=case_id, posts=_posts())
        stats = await ingest_after_crawl(repository, social, case_id, _posts())
        assert stats["accounts"] == 2
        assert stats["media"]["created"] >= 1
    finally:
        await database.dispose()


async def test_ingest_propagation_nodes(tmp_path: Path) -> None:
    database, repository, social, case_id = await _setup(tmp_path)
    try:
        await social.persist_batch(case_id=case_id, posts=_posts())
        posts = await social.list_posts_by_case(case_id)
        graph = {
            "algorithm_version": "1.1.0",
            "node_roles": [
                {
                    "post_id": posts[0].native_id,
                    "role": "source",
                    "score": 0.8,
                    "out_degree": 1,
                    "in_degree": 0,
                }
            ],
        }
        written = await ingest_propagation_nodes(
            repository, social, case_id, graph
        )
        assert written == 1
        nodes = await repository.list_propagation_nodes(case_id)
        assert nodes[0].role == "source"
        assert nodes[0].post_id == posts[0].id
    finally:
        await database.dispose()


def test_artifact_references_collects_ids() -> None:
    refs = artifact_references(
        {
            "cards": [
                {
                    "id": "claim-1",
                    "supporting_evidence": ["ev-1"],
                    "contradicting_evidence": [],
                    "source_post_id": "p1",
                }
            ],
            "edges": [{"edge_id": "e1", "source": "p1", "target": "p2"}],
            "citation_links": [{"conclusion": "x", "evidence_ids": ["ev-1"]}],
        }
    )
    assert "ev-1" in refs["evidence_ids"]
    assert "claim-1" in refs["claim_ids"]
    assert "e1" in refs["edge_ids"]
    assert "p1" in refs["post_ids"]


async def test_persist_run_cost_summary(tmp_path: Path) -> None:
    database, repository, _social, case_id = await _setup(tmp_path)
    try:
        run = await repository.create_agent_run(
            case_id=case_id, turn_id=None, objective="费用", metadata={}
        )
        await persist_run_cost_summary(repository, run.id, case_id)
        summaries = await repository.list_cost_summaries(run_id=run.id)
        assert len(summaries) == 1
        assert summaries[0].summary_type == "run"
        assert summaries[0].total_cost == 0
    finally:
        await database.dispose()
