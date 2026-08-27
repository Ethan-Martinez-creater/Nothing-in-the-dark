"""M7a domain models: accounts, media assets, entities, propagation nodes,
evaluations and cost summaries through the repository layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.repositories import ApplicationRepository
from app.infrastructure.database import Database
from app.schemas.cases import CreateCaseRequest


async def _setup(tmp_path) -> tuple[ApplicationRepository, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'domain.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="领域模型测试", platforms=["weibo", "bilibili"])
    )
    return repository, case.id


# ---------- accounts ----------


async def test_upsert_account_creates_and_updates_idempotently(tmp_path) -> None:
    repository, _ = await _setup(tmp_path)
    first = await repository.upsert_account(
        case_id=None,
        platform="weibo",
        native_id="official_1",
        name="人民日报",
        normalized_name="人民日报",
        follower_count=100,
        is_authoritative=True,
    )
    second = await repository.upsert_account(
        case_id=None,
        platform="weibo",
        native_id="official_1",
        name="人民日报",
        normalized_name="人民日报",
        follower_count=200,
    )
    assert first.id == second.id
    assert second.follower_count == 200
    # is_authoritative is sticky once set
    assert second.is_authoritative is True


async def test_list_authoritative_accounts_filters_whitelist(tmp_path) -> None:
    repository, _ = await _setup(tmp_path)
    await repository.upsert_account(
        case_id=None,
        platform="weibo",
        native_id="off1",
        name="官方账号",
        normalized_name="官方账号",
        is_authoritative=True,
    )
    await repository.upsert_account(
        case_id=None,
        platform="weibo",
        native_id="user1",
        name="普通用户",
        normalized_name="普通用户",
    )
    authoritative = await repository.list_authoritative_accounts()
    assert [account.native_id for account in authoritative] == ["off1"]


# ---------- media assets ----------


async def test_media_asset_deduplicated_per_post_and_searchable_by_url(tmp_path) -> None:
    repository, case_id = await _setup(tmp_path)
    first = await repository.create_media_asset(
        case_id=case_id,
        post_id="post-1",
        platform="weibo",
        media_type="image",
        url="https://cdn.example.com/img/a.png?token=abc",
        normalized_url="https://cdn.example.com/img/a.png",
        phash="a1b2c3d4",
    )
    duplicate = await repository.create_media_asset(
        case_id=case_id,
        post_id="post-1",
        platform="weibo",
        media_type="image",
        url="https://cdn.example.com/img/a.png?token=xyz",
        normalized_url="https://cdn.example.com/img/a.png",
    )
    assert duplicate.id == first.id  # same (case, url, post) is idempotent

    await repository.create_media_asset(
        case_id=case_id,
        post_id="post-2",
        platform="bilibili",
        media_type="image",
        url="https://cdn.example.com/img/a.png?size=full",
        normalized_url="https://cdn.example.com/img/a.png",
    )
    # The same normalized URL now appears on two different posts: a
    # cross-platform same-media signal for the propagation algorithm.
    matches = await repository.list_media_assets_by_url(
        case_id, "https://cdn.example.com/img/a.png"
    )
    assert {asset.post_id for asset in matches} == {"post-1", "post-2"}


# ---------- entities ----------


async def test_entity_mentions_accumulate_and_merge_aliases(tmp_path) -> None:
    repository, case_id = await _setup(tmp_path)
    seen = datetime.now(UTC)
    first = await repository.upsert_entity(
        case_id=case_id,
        entity_type="person",
        name="张三",
        normalized_name="张三",
        aliases=["张三"],
        seen_at=seen,
    )
    later = seen + timedelta(hours=2)
    second = await repository.upsert_entity(
        case_id=case_id,
        entity_type="person",
        name="张三",
        normalized_name="张三",
        aliases=["张三丰"],
        seen_at=later,
    )
    assert second.id == first.id
    assert second.mentions_count == 2
    assert set(second.aliases) == {"张三", "张三丰"}
    assert second.last_seen_at == later
    assert second.first_seen_at == seen


async def test_list_entities_orders_by_mentions(tmp_path) -> None:
    repository, case_id = await _setup(tmp_path)
    await repository.upsert_entity(
        case_id=case_id, entity_type="org", name="机构A", normalized_name="机构a"
    )
    await repository.upsert_entity(
        case_id=case_id, entity_type="org", name="机构A", normalized_name="机构a"
    )
    await repository.upsert_entity(
        case_id=case_id, entity_type="org", name="机构B", normalized_name="机构b"
    )
    entities = await repository.list_entities(case_id, entity_type="org")
    assert [entity.name for entity in entities] == ["机构A", "机构B"]


# ---------- propagation nodes ----------


async def test_propagation_node_idempotent_and_listable(tmp_path) -> None:
    repository, case_id = await _setup(tmp_path)
    first = await repository.create_propagation_node(
        case_id=case_id,
        post_id="post-1",
        role="source",
        score=0.9,
        attributes={"out_degree": 3},
    )
    duplicate = await repository.create_propagation_node(
        case_id=case_id,
        post_id="post-1",
        role="source",
        score=0.5,
    )
    assert duplicate.id == first.id

    await repository.create_propagation_node(
        case_id=case_id, post_id="post-2", role="bridge", score=0.7
    )
    sources = await repository.list_propagation_nodes(case_id, role="source")
    assert [node.post_id for node in sources] == ["post-1"]


# ---------- evaluations ----------


async def test_evaluations_created_and_filtered(tmp_path) -> None:
    repository, case_id = await _setup(tmp_path)
    await repository.create_evaluation(
        case_id=case_id,
        run_id=None,
        metric="propagation_precision",
        score=0.8,
        details={"edges": 10},
    )
    await repository.create_evaluation(
        case_id=case_id,
        run_id=None,
        metric="source_topk",
        score=1.0,
    )
    all_evaluations = await repository.list_evaluations(case_id=case_id)
    assert len(all_evaluations) == 2
    precision = await repository.list_evaluations(
        case_id=case_id, metric="propagation_precision"
    )
    assert precision[0].score == 0.8
    assert precision[0].details == {"edges": 10}


# ---------- cost summaries ----------


async def test_cost_summary_upsert_keeps_single_row_per_run(tmp_path) -> None:
    repository, case_id = await _setup(tmp_path)
    first = await repository.upsert_cost_summary(
        summary_type="run",
        run_id="run-1",
        case_id=case_id,
        model_cost=0.3,
        tool_cost=0.2,
        total_cost=0.5,
    )
    second = await repository.upsert_cost_summary(
        summary_type="run",
        run_id="run-1",
        case_id=case_id,
        model_cost=0.6,
        tool_cost=0.4,
        total_cost=1.0,
    )
    assert second.id == first.id
    assert second.total_cost == 1.0
