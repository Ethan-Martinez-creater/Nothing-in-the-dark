"""V3 §78: Workspace Entity tests (E01–E14)。

内存 SQLite；覆盖 deterministic identity、name 不合并、可撤销 same_as
relation、retract 传播、case link reconciliation、orphan cleanup、
profile / risk / coordination 复用。
"""

from __future__ import annotations

from typing import Any

from app.application.repositories import ApplicationRepository
from app.application.workspace_entity_service import WorkspaceEntityService
from app.infrastructure.database.alignment_repository import AlignmentRepository
from app.infrastructure.database.integrity_repository import IntegrityRepository
from app.infrastructure.database.models import AccountRecord
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.database.workspace_entity_repository import (
    WorkspaceEntityRepository,
)
from app.schemas.cases import CreateCaseRequest
from tests.memory_db import MemoryDatabase


async def _setup() -> Any:
    from types import SimpleNamespace

    database = MemoryDatabase()
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    workspace_repo = WorkspaceEntityRepository(database)
    alignment_repo = AlignmentRepository(database)
    integrity_repo = IntegrityRepository(database)
    social_repo = SocialRepository(database)
    service = WorkspaceEntityService(
        workspace_repository=workspace_repo,
        alignment_repository=alignment_repo,
        application_repository=app_repo,
        social_repository=social_repo,
        integrity_repository=integrity_repo,
        database=database,
    )
    case = await app_repo.create_case(
        CreateCaseRequest(
            topic="调查A",
            platforms=["weibo"],
            time_start="2026-08-10",
            time_end="2026-08-20",
        )
    )
    return SimpleNamespace(
        db=database,
        app=app_repo,
        workspace=workspace_repo,
        alignment=alignment_repo,
        integrity=integrity_repo,
        social=social_repo,
        service=service,
        case=case,
    )


async def _make_case(env: Any, topic: str) -> Any:
    return await env.app.create_case(
        CreateCaseRequest(
            topic=topic,
            platforms=["weibo"],
            time_start="2026-08-10",
            time_end="2026-08-20",
        )
    )


async def _account(
    env: Any, case_id: str, platform: str, native_id: str, name: str
) -> AccountRecord:
    return await env.app.upsert_account(
        case_id=case_id,
        platform=platform,
        native_id=native_id,
        name=name,
        normalized_name=name.lower(),
    )


def _post(
    platform: str,
    native_id: str,
    author_id: str,
    author_name: str,
) -> dict[str, Any]:
    return {
        "platform": platform,
        "native_id": native_id,
        "content_type": "post",
        "title": "",
        "content": f"{platform} 事件讨论 {native_id}",
        "author": author_name,
        "published_at": "2026-08-15T10:00:00+08:00",
        "engagement": 10,
        "metrics": {"total": 10},
        "url": f"https://example.com/{platform}/{native_id}",
        # persist_batch 从 raw 的 creator_hash/user_id/uid 提取 author_id
        "raw": {"id": native_id, "user_id": author_id},
        "comments": [],
    }


# ---------------------------------------------------------------------------
# E01–E03: deterministic identity
# ---------------------------------------------------------------------------


async def test_e01_same_platform_native_id_same_entity() -> None:
    env = await _setup()
    case_b = await _make_case(env, "调查B")
    await _account(env, env.case.id, "weibo", "123", "账号甲")
    # Case B 采集到同一账号的帖子（AccountRecord 全局唯一，Case B 的
    # appearance 通过 SourcePost.author 维度产生）
    await env.social.persist_batch(
        case_id=case_b.id,
        posts=[_post("weibo", "b1", "123", "账号甲")],
    )
    await env.service.refresh_case(env.case.id)
    await env.service.refresh_case(case_b.id)
    entities = await env.workspace.list(limit=50)
    matches = [
        entity
        for entity in entities
        if entity.canonical_name == "账号甲"
    ]
    assert len(matches) == 1
    profile = await env.service.get_profile(matches[0].id)
    assert profile["investigation_count"] == 2
    await env.db.dispose()


async def test_e02_same_display_name_different_native_id_not_merged() -> None:
    env = await _setup()
    case_b = await _make_case(env, "调查B")
    await _account(env, env.case.id, "weibo", "111", "张三")
    await _account(env, case_b.id, "weibo", "222", "张三")
    await env.service.refresh_case(env.case.id)
    await env.service.refresh_case(case_b.id)
    entities = await env.workspace.list(limit=50)
    assert len(entities) == 2
    await env.db.dispose()


async def test_e03_concurrent_create_same_key_single_entity() -> None:
    env = await _setup()
    await _account(env, env.case.id, "weibo", "999", "并发账号")
    first = await env.workspace.create_with_key(
        canonical_name="并发账号",
        key_type="platform_account",
        key_value="weibo:999",
    )
    second = await env.workspace.create_with_key(
        canonical_name="并发账号",
        key_type="platform_account",
        key_value="weibo:999",
    )
    assert first.id == second.id
    await env.db.dispose()


# ---------------------------------------------------------------------------
# E04–E07: reversible same_as relation / retract propagation
# ---------------------------------------------------------------------------


async def _setup_canonical_two_accounts(env: Any) -> tuple[AccountRecord, AccountRecord]:
    account_x = await _account(env, env.case.id, "weibo", "xa", "主体X")
    account_y = await _account(env, env.case.id, "bilibili", "ya", "主体Y")
    canonical = await env.alignment.upsert_canonical_entity(
        case_id=env.case.id,
        entity_type="account",
        canonical_name="跨平台主体XY",
    )
    for account in (account_x, account_y):
        await env.alignment.create_entity_mention(
            case_id=env.case.id,
            entity_id=canonical.id,
            platform_object_type="account",
            platform_object_id=account.id,
        )
    return account_x, account_y


async def test_e04_active_canonical_mentions_create_active_relation() -> None:
    env = await _setup()
    await _setup_canonical_two_accounts(env)
    result = await env.service.refresh_case(env.case.id)
    assert result["relations_upserted"] >= 1
    entities = await env.workspace.list(limit=50)
    assert len(entities) == 2
    relations = await env.workspace.list_active_relations_for_entities(
        [entity.id for entity in entities]
    )
    assert len(relations) == 1
    assert relations[0].status == "active"
    # identity component 合并
    component = await env.service.identity_component(entities[0].id)
    assert len(component["entity_ids"]) == 2
    await env.db.dispose()


async def test_e05_retract_materialization_retracts_relation() -> None:
    env = await _setup()
    await _setup_canonical_two_accounts(env)
    await env.service.refresh_case(env.case.id)
    entities = await env.workspace.list(limit=50)
    assert len(entities) == 2
    # retract：删除 canonical entity + mentions（等价于 materialization retract）
    async with env.db.session_factory() as session:
        from sqlalchemy import delete, select

        from app.infrastructure.database.models import (
            CanonicalEntityRecord,
            EntityMentionRecord,
        )

        canonical = await session.scalar(
            select(CanonicalEntityRecord).where(
                CanonicalEntityRecord.case_id == env.case.id,
                CanonicalEntityRecord.entity_type == "account",
            )
        )
        assert canonical is not None
        await session.execute(
            delete(EntityMentionRecord).where(
                EntityMentionRecord.entity_id == canonical.id
            )
        )
        await session.execute(
            delete(CanonicalEntityRecord).where(
                CanonicalEntityRecord.id == canonical.id
            )
        )
        await session.commit()
    result = await env.service.refresh_case(env.case.id)
    assert result["relations_retracted"] >= 1
    relations = await env.workspace.list_active_relations_for_entities(
        [entity.id for entity in entities]
    )
    assert relations == []
    await env.db.dispose()


async def test_e06_retracted_relation_splits_component() -> None:
    env = await _setup()
    await _setup_canonical_two_accounts(env)
    await env.service.refresh_case(env.case.id)
    entities = await env.workspace.list(limit=50)
    # retract 全部 relation（模拟 retract 后 refresh）
    async with env.db.session_factory() as session:
        from sqlalchemy import update

        from app.infrastructure.database.models import WorkspaceEntityRelationRecord

        await session.execute(
            update(WorkspaceEntityRelationRecord).values(status="retracted")
        )
        await session.commit()
    component = await env.service.identity_component(entities[0].id)
    assert component["entity_ids"] == [entities[0].id]
    await env.db.dispose()


async def test_e07_unconfirmed_candidate_no_relation() -> None:
    env = await _setup()
    # 只有 Alignment candidate（未 materialize canonical entity）→ 无 relation
    await _account(env, env.case.id, "weibo", "p1", "账号1")
    await _account(env, env.case.id, "bilibili", "p2", "账号2")
    await env.alignment.create_alignment_candidate(
        case_id=env.case.id,
        left_type="account",
        left_id="weibo:p1",
        right_type="account",
        right_id="bilibili:p2",
        combined_score=0.9,
        decision="pending",
    )
    await env.service.refresh_case(env.case.id)
    relations = await env.workspace.list_active_relations_for_entities(
        [entity.id for entity in await env.workspace.list(limit=50)]
    )
    assert relations == []
    await env.db.dispose()


# ---------------------------------------------------------------------------
# E08–E10: reconciliation / unique / guard
# ---------------------------------------------------------------------------


async def test_e08_stale_account_removed_case_link_removed() -> None:
    env = await _setup()
    account = await _account(env, env.case.id, "weibo", "gone", "将被删除")
    await env.service.refresh_case(env.case.id)
    entity = await env.workspace.find_by_key("platform_account", "weibo:gone")
    assert entity is not None
    links = await env.workspace.list_case_links(entity.id)
    assert len(links) == 1
    # 删除 account → refresh → stale case link 被清除
    async with env.db.session_factory() as session:
        record = await session.get(AccountRecord, account.id)
        assert record is not None
        await session.delete(record)
        await session.commit()
    result = await env.service.refresh_case(env.case.id)
    assert result["links_removed"] >= 1
    assert await env.workspace.list_case_links(entity.id) == []
    await env.db.dispose()


async def test_e09_case_link_unique() -> None:
    env = await _setup()
    await _account(env, env.case.id, "weibo", "u1", "唯一链接")
    await env.service.refresh_case(env.case.id)
    await env.service.refresh_case(env.case.id)
    entity = await env.workspace.find_by_key("platform_account", "weibo:u1")
    assert entity is not None
    links = await env.workspace.list_case_links(entity.id)
    assert len(links) == 1
    await env.db.dispose()


async def test_e10_identity_component_max_guard() -> None:
    database = MemoryDatabase()
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    service = WorkspaceEntityService(
        workspace_repository=WorkspaceEntityRepository(database),
        alignment_repository=AlignmentRepository(database),
        application_repository=app_repo,
        social_repository=SocialRepository(database),
        integrity_repository=IntegrityRepository(database),
        database=database,
        max_component_nodes=4,
    )
    case = await app_repo.create_case(
        CreateCaseRequest(topic="守卫", platforms=["weibo"])
    )
    for index in range(6):
        await app_repo.upsert_account(
            case_id=case.id,
            platform="weibo",
            native_id=f"g{index}",
            name=f"守卫{index}",
            normalized_name=f"守卫{index}",
        )
    entities = []
    for index in range(6):
        entity = await WorkspaceEntityRepository(database).create_with_key(
            canonical_name=f"守卫{index}",
            key_type="platform_account",
            key_value=f"weibo:g{index}",
        )
        entities.append(entity)
    workspace = WorkspaceEntityRepository(database)
    for left, right in zip(entities, entities[1:], strict=False):
        await workspace.upsert_relation(
            left_entity_id=left.id,
            right_entity_id=right.id,
            relation_type="same_as",
            source_case_id=case.id,
            source_type="canonical_entity",
            source_id="guard",
        )
    import pytest

    from app.core.errors import ApplicationError

    with pytest.raises(ApplicationError) as exc_info:
        await service.identity_component(entities[0].id)
    assert exc_info.value.code == "identity_component_too_large"
    await database.dispose()


# ---------------------------------------------------------------------------
# E11–E14: profile / risk / coordination
# ---------------------------------------------------------------------------


async def test_e11_profile_investigation_count_correct() -> None:
    env = await _setup()
    case_b = await _make_case(env, "调查C")
    await _account(env, env.case.id, "weibo", "shared", "共享账号")
    await env.social.persist_batch(
        case_id=case_b.id,
        posts=[_post("weibo", "c1", "shared", "共享账号")],
    )
    await env.service.refresh_case(env.case.id)
    await env.service.refresh_case(case_b.id)
    entity = (await env.workspace.list(limit=50))[0]
    profile = await env.service.get_profile(entity.id)
    assert profile["investigation_count"] == 2
    assert set(profile["investigations"]) == {env.case.id, case_b.id}
    await env.db.dispose()


async def test_e12_exact_platform_risk_reused() -> None:
    env = await _setup()
    await _account(env, env.case.id, "weibo", "risky", "风险账号")
    await env.service.refresh_case(env.case.id)
    entity = (await env.workspace.list(limit=50))[0]
    await env.integrity.upsert_risk_assessment(
        case_id=env.case.id,
        subject_type="account",
        subject_id="weibo:risky",
        risk_type="automation",
        score=0.9,
        band="high",
    )
    profile = await env.service.get_profile(entity.id)
    assert len(profile["risk_assessments"]) == 1
    assert profile["risk_assessments"][0]["subject_id"] == "weibo:risky"
    await env.db.dispose()


async def test_e13_name_only_risk_not_promoted_cross_case() -> None:
    env = await _setup()
    await _account(env, env.case.id, "weibo", "nm", "同名者")
    await env.service.refresh_case(env.case.id)
    entity = (await env.workspace.list(limit=50))[0]
    # name-only subject（platform:author_name 风格）不匹配 platform_account key
    await env.integrity.upsert_risk_assessment(
        case_id=env.case.id,
        subject_type="account",
        subject_id="weibo:同名者",
        risk_type="automation",
        score=0.8,
        band="high",
    )
    profile = await env.service.get_profile(entity.id)
    assert profile["risk_assessments"] == []
    assert len(profile["unresolved_local_risk"]) == 1
    await env.db.dispose()


async def test_e14_coordination_cluster_membership_reused() -> None:
    env = await _setup()
    await _account(env, env.case.id, "weibo", "mem", "集群成员")
    await env.service.refresh_case(env.case.id)
    entity = (await env.workspace.list(limit=50))[0]
    cluster = await env.integrity.create_cluster(
        case_id=env.case.id,
        size=3,
        score=0.9,
        members=[
            {"account_id": "weibo:mem"},
            {"account_id": "weibo:other1"},
            {"account_id": "weibo:other2"},
        ],
    )
    assert cluster.id
    profile = await env.service.get_profile(entity.id)
    assert len(profile["coordination_memberships"]) == 1
    assert profile["coordination_memberships"][0]["cluster_id"] == cluster.id
    await env.db.dispose()


async def test_e15_unresolvable_name_only_risk_ignored() -> None:
    """Rework R10：name-only 风险只有 platform 一致且名字精确匹配才进
    unresolved；无法可靠归属的 assessment 直接忽略。"""
    env = await _setup()
    await _account(env, env.case.id, "weibo", "w1", "精确名字")
    await env.service.refresh_case(env.case.id)
    entity = (await env.workspace.list(limit=50))[0]
    # platform 不一致（bilibili vs weibo）→ 忽略
    await env.integrity.upsert_risk_assessment(
        case_id=env.case.id,
        subject_type="account",
        subject_id="bilibili:精确名字",
        risk_type="automation",
        score=0.8,
        band="high",
    )
    # platform 一致但名字不精确（fuzzy 前缀不算命中）→ 忽略
    await env.integrity.upsert_risk_assessment(
        case_id=env.case.id,
        subject_type="account",
        subject_id="weibo:精确名字粉丝团",
        risk_type="automation",
        score=0.7,
        band="high",
    )
    # platform 一致 + strip/casefold 精确相等（带空白）→ unresolved
    await env.integrity.upsert_risk_assessment(
        case_id=env.case.id,
        subject_type="account",
        subject_id="weibo: 精确名字 ",
        risk_type="automation",
        score=0.6,
        band="high",
    )
    profile = await env.service.get_profile(entity.id)
    assert profile["risk_assessments"] == []
    unresolved = profile["unresolved_local_risk"]
    assert len(unresolved) == 1
    assert unresolved[0]["subject_id"] == "weibo: 精确名字 "
    await env.db.dispose()
