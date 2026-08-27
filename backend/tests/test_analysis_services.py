"""Real algorithms: classifier-backed opinion, claim persistence, edges."""

from __future__ import annotations

from pathlib import Path

from app.application.repositories import ApplicationRepository
from app.harness.tool_factory import _persist_propagation_edges
from app.infrastructure.database import Database
from app.infrastructure.database.social_repository import SocialRepository
from app.schemas.cases import CreateCaseRequest
from app.services.analysis import (
    analyze_opinion,
    build_report,
    reconstruct_propagation,
    verify_claims,
)
from app.services.classifiers import ModelClassification
from app.services.propagation_algorithm import ALGORITHM_VERSION


def sample_posts() -> list[dict[str, object]]:
    return [
        {
            "id": "post-1",
            "platform": "weibo",
            "author": "source",
            "content": "first account",
            "published_at": "2026-01-01T00:00:00+00:00",
            "sentiment": "neutral",
            "engagement": 100,
            "is_demo": True,
        },
        {
            "id": "post-2",
            "platform": "bilibili",
            "author": "reviewer",
            "content": "second account",
            "published_at": "2026-01-01T01:00:00+00:00",
            "sentiment": "negative",
            "engagement": 200,
            "is_demo": True,
        },
    ]


async def test_analysis_pipeline_returns_structured_results() -> None:
    posts = sample_posts()
    opinion = analyze_opinion(posts)
    propagation = await reconstruct_propagation(posts)
    fact_check = await verify_claims(posts, "测试主题")
    report = build_report("测试主题", opinion, propagation, fact_check)

    assert sum(opinion["sentiment_distribution"].values()) == 100
    assert len(propagation["nodes"]) == 2
    # Two posts on different platforms with no explicit relation: no candidate
    # edge may be invented, and posting order alone cannot yield observed edges.
    assert propagation["edges"] == []
    assert propagation["origin_candidates"][0]["node_id"] == "post-1"
    assert all(card["verdict"] == "insufficient" for card in fact_check["cards"])
    assert fact_check["cards"][0]["source_post_id"] == "post-1"
    assert report["is_demo"] is True
    assert "citation_links" in report


async def test_build_report_tolerates_wrapped_artifact_input() -> None:
    """report 专家可能把 get_artifact 的包装结构（{"artifact": {"data": …}}）
    而非 artifact.data 直接传入 build_report；不得 KeyError（2026-08-08
    冒烟 BUG-4：report 专家两次委派均因此失败）。"""
    posts = sample_posts()
    opinion = analyze_opinion(posts)
    propagation = await reconstruct_propagation(posts)
    fact_check = await verify_claims(posts, "测试主题")
    wrapped = {"artifact": {"data": propagation}}
    report = build_report("测试主题", opinion, wrapped, fact_check)
    assert "传播链路" in [s["title"] for s in report["sections"]]
    section = next(s for s in report["sections"] if s["title"] == "传播链路")
    assert "2 个传播节点" in section["content"]

    # 缺 nodes/edges 键也不应崩溃。
    degraded = build_report("测试主题", opinion, {"algorithm_version": "x"}, fact_check)
    assert any("0 个传播节点" in s["content"] for s in degraded["sections"])


async def test_analysis_never_emits_fixed_conclusions() -> None:
    """The statistical helpers must not contain narrative text unrelated to data."""
    posts = sample_posts()
    opinion = analyze_opinion(posts)
    fact_check = await verify_claims(posts, "测试主题")

    assert "summary" not in opinion
    assert "key_findings" not in opinion
    assert opinion["statistics"]["total_posts"] == 2
    assert fact_check["cards"][0]["claim"] == posts[0]["content"]


async def test_analyze_opinion_uses_real_classification_not_post_field() -> None:
    """The stored ``sentiment`` field must never be trusted directly."""
    posts = sample_posts()
    posts[0]["content"] = "非常满意，值得推荐"
    posts[0]["sentiment"] = "negative"  # stale/misleading field value
    opinion = analyze_opinion(posts)
    distribution = opinion["sentiment_distribution"]
    assert distribution["positive"] == 50.0
    assert distribution["negative"] == 0.0
    assert opinion["statistics"]["stance_distribution"]["supportive"] == 50.0


def test_analyze_opinion_accepts_model_classifications() -> None:
    posts = sample_posts()
    classifications = [
        ModelClassification(
            sentiment="positive",
            score=0.9,
            confidence=0.97,
            stance="supportive",
            source="model",
        ),
        ModelClassification(
            sentiment="negative",
            score=-0.8,
            confidence=0.95,
            stance="opposing",
            source="model",
        ),
    ]
    opinion = analyze_opinion(posts, classifications=classifications)
    assert opinion["sentiment_distribution"]["positive"] == 50.0
    assert opinion["statistics"]["intensity_distribution"]["strong"] == 100.0


async def test_reconstruct_propagation_observed_edges_need_explicit_relation() -> None:
    posts = sample_posts()
    posts[1]["reply_to_id"] = "post-1"
    propagation = await reconstruct_propagation(posts)
    assert len(propagation["edges"]) == 1
    edge = propagation["edges"][0]
    assert edge["relation"] == "observed"
    assert edge["source"] == "post-1"
    assert edge["target"] == "post-2"
    assert edge["algorithm_version"] == ALGORITHM_VERSION
    assert edge["feature_scores"]["explicit_relation"] == 1.0


async def test_reconstruct_propagation_inferred_entity_overlap() -> None:
    """Inferred edges need real semantic signals, not adjacency alone."""
    posts = sample_posts()
    posts[1]["platform"] = "weibo"
    posts[0]["content"] = "2026年3月15日发生事故，官方回应如下"
    posts[1]["content"] = "2026年3月15日的事故仍在发酵"
    propagation = await reconstruct_propagation(posts)
    inferred = [edge for edge in propagation["edges"] if edge["relation"] == "inferred"]
    assert len(inferred) == 1
    edge = inferred[0]
    assert edge["source"] == "post-1"
    assert edge["target"] == "post-2"
    assert edge["feature_scores"]["entity_overlap"] > 0
    assert edge["confidence"] > 0.35


async def test_reconstruct_propagation_ignores_old_unrelated_posts() -> None:
    """Posts outside the time window or without signals produce no edge."""
    posts = sample_posts()
    posts[1]["platform"] = "weibo"
    posts[1]["published_at"] = "2026-04-01T00:00:00+00:00"  # > 7 days later
    propagation = await reconstruct_propagation(posts)
    assert all(edge["relation"] == "observed" for edge in propagation["edges"])


async def test_verify_claims_persists_claims(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'claims.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="持久化测试", platforms=["weibo"])
        )
        run = await repository.create_agent_run(
            case_id=case.id,
            turn_id=None,
            objective="主张抽取",
        )
        posts = sample_posts()
        result = await verify_claims(
            posts,
            "测试主题",
            repository=repository,
            case_id=case.id,
            created_by_run_id=run.id,
        )
        assert result["claim_extraction"]["persisted"] is True
        assert not any(card["id"].startswith("claim-") for card in result["cards"])
        claims = await repository.list_claims_by_case(case.id)
        assert len(claims) == len(result["cards"])
        claim_ids = {claim.id for claim in claims}
        assert {card["id"] for card in result["cards"]} <= claim_ids
        # Every persisted claim gets its source post as first evidence.
        evidence = await repository.list_evidence_by_case(case.id)
        assert len(evidence) == len(claims)
        assert all(item.claim_id in claim_ids for item in evidence)
        assert all(item.source_type == "post" for item in evidence)
    finally:
        await database.dispose()


async def test_propagation_edges_persisted_idempotently(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'edges.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="边持久化测试", platforms=["weibo"])
        )
        posts = sample_posts()
        posts[1]["reply_to_id"] = "post-1"
        await social.persist_batch(case_id=case.id, posts=posts)
        graph = await reconstruct_propagation(posts)
        native_to_db = {
            str(post.native_id): post.id
            for post in await social.list_posts_by_case(case.id)
        }
        edge = graph["edges"][0]
        await repository.create_propagation_edge(
            case_id=case.id,
            source_post_id=native_to_db[edge["source"]],
            target_post_id=native_to_db[edge["target"]],
            relation=edge["relation"],
            confidence=edge["confidence"],
            feature_scores=edge["feature_scores"],
            evidence_ids=edge["evidence_ids"],
            algorithm_version=edge["algorithm_version"],
        )
        # Idempotent: the same edge returns the existing row.
        duplicate = await repository.create_propagation_edge(
            case_id=case.id,
            source_post_id=native_to_db[edge["source"]],
            target_post_id=native_to_db[edge["target"]],
            relation=edge["relation"],
            confidence=edge["confidence"],
            feature_scores=edge["feature_scores"],
            evidence_ids=edge["evidence_ids"],
        )
        stored = await repository.list_propagation_edges_by_case(case.id)
        assert len(stored) == 1
        assert stored[0].id == duplicate.id
        assert stored[0].algorithm_version == ALGORITHM_VERSION
    finally:
        await database.dispose()


async def test_propagation_persist_backfills_edge_id(tmp_path: Path) -> None:
    """``_persist_propagation_edges`` 把数据库边 id 回填进 Artifact 图数据，
    前端传播边确认按钮才能定位边（幂等重跑时 id 保持稳定）。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'backfill.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="回填测试", platforms=["weibo"])
        )
        posts = sample_posts()
        posts[1]["reply_to_id"] = "post-1"
        await social.persist_batch(case_id=case.id, posts=posts)
        graph = await reconstruct_propagation(posts)
        await _persist_propagation_edges(
            graph,
            repository=repository,
            social=social,
            case_id=case.id,
        )
        stored = await repository.list_propagation_edges_by_case(case.id)
        assert len(stored) == len(graph["edges"])
        stored_ids = {record.id for record in stored}
        assert {edge["edge_id"] for edge in graph["edges"]} == stored_ids
        # 幂等重跑：回填 id 与数据库行均不变。
        first_ids = [edge["edge_id"] for edge in graph["edges"]]
        await _persist_propagation_edges(
            graph,
            repository=repository,
            social=social,
            case_id=case.id,
        )
        assert [edge["edge_id"] for edge in graph["edges"]] == first_ids
        assert len(await repository.list_propagation_edges_by_case(case.id)) == len(
            first_ids
        )
    finally:
        await database.dispose()


async def test_propagation_persist_resolves_prefixed_db_ids(tmp_path: Path) -> None:
    """RAG/查询路径的图端点形如 ``social_post:{db_id}``（带前缀的数据库
    主键，而非原生 id），同样必须回填 edge_id 才能让前端确认按钮可用。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'prefixed.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="前缀回填测试", platforms=["weibo"])
        )
        posts = sample_posts()
        posts[1]["reply_to_id"] = "post-1"
        await social.persist_batch(case_id=case.id, posts=posts)
        records = await social.list_posts_by_case(case.id)
        graph = await reconstruct_propagation(posts)
        # 模拟 RAG 路径产物：端点改成 "social_post:{db_id}" 前缀格式。
        by_native = {
            str(post["id"]): record.id
            for post, record in zip(posts, records, strict=True)
        }
        for edge in graph["edges"]:
            edge["source"] = f"social_post:{by_native[str(edge['source'])]}"
            edge["target"] = f"social_post:{by_native[str(edge['target'])]}"
        await _persist_propagation_edges(
            graph,
            repository=repository,
            social=social,
            case_id=case.id,
        )
        stored = await repository.list_propagation_edges_by_case(case.id)
        assert len(stored) == len(graph["edges"])
        stored_ids = {record.id for record in stored}
        assert {edge["edge_id"] for edge in graph["edges"]} == stored_ids
    finally:
        await database.dispose()
