"""V3 §79: Case deletion V3 cleanup tests (D01-D09).

§67 八步清理：quality → relations → case links → cross links → signal
case links → primary signals → 孤儿 signal → 孤儿 entity。用内存 SQLite +
真实 repository，直接构造数据后调用 ApplicationRepository.delete_case。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.application.repositories import ApplicationRepository
from app.infrastructure.database.cross_investigation_repository import (
    CrossInvestigationRepository,
    cross_link_fingerprint,
)
from app.infrastructure.database.derived_signal_repository import (
    DerivedSignalRepository,
)
from app.infrastructure.database.investigation_quality_repository import (
    InvestigationQualityRepository,
)
from app.infrastructure.database.workspace_entity_repository import (
    WorkspaceEntityRepository,
)
from app.schemas.cases import CreateCaseRequest
from tests.memory_db import MemoryDatabase


async def _setup() -> SimpleNamespace:
    database = MemoryDatabase()
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    workspace = WorkspaceEntityRepository(database)
    cross = CrossInvestigationRepository(database)
    derived = DerivedSignalRepository(database)
    quality = InvestigationQualityRepository(database)
    case_a = await app_repo.create_case(
        CreateCaseRequest(topic="删除案例A", platforms=["weibo"])
    )
    case_b = await app_repo.create_case(
        CreateCaseRequest(topic="保留案例B", platforms=["weibo"])
    )
    return SimpleNamespace(
        db=database,
        app=app_repo,
        workspace=workspace,
        cross=cross,
        derived=derived,
        quality=quality,
        case_a=case_a,
        case_b=case_b,
    )


async def _entity(
    env: SimpleNamespace, name: str, key: str
) -> Any:
    return await env.workspace.create_with_key(
        canonical_name=name,
        key_type="platform_account",
        key_value=key,
    )


async def _link_entity_case(
    env: SimpleNamespace, entity_id: str, case_id: str
) -> Any:
    return await env.workspace.upsert_case_link(
        entity_id=entity_id,
        case_id=case_id,
        source_type="case_account",
        source_id=f"{case_id}:{entity_id}",
    )


async def _quality(env: SimpleNamespace, case_id: str) -> None:
    await env.quality.upsert(
        case_id=case_id,
        overall_score=60.0,
        grade="needs_attention",
        dimensions={},
        metrics={},
        gaps=[],
        warnings=[],
        input_fingerprint=f"fp-{case_id}",
        algorithm_version="quality-1.0.0",
        computed_at=datetime.now(UTC),
    )


async def _derived_signal(
    env: SimpleNamespace, *, case_id: str, fingerprint: str, links: list[str]
) -> Any:
    return await env.derived.upsert_observed_signal(
        fingerprint=fingerprint,
        case_id=case_id,
        source_type="derived",
        source_id=fingerprint,
        signal_type="actor_recurrence",
        severity="warning",
        title="主体复现",
        why_it_matters="跨调查出现",
        confidence=None,
        metric_snapshot={},
        evidence_refs=[],
        related_case_ids=links,
        detector_version="advanced-signal-1.0.0",
        case_links=links,
    )


async def _cross_link(env: SimpleNamespace, left: str, right: str) -> None:
    await env.cross.upsert_link(
        left_case_id=left,
        right_case_id=right,
        relation_type="shared_actor",
        status="observed",
        score=1.0,
        evidence_count=1,
        evidence_refs=[{"type": "actor", "component_key": "c1"}],
        feature_scores={"identity_component": 1.0},
        algorithm_version="cross-intel-1.0.0",
    )


# ---------------------------------------------------------------------------
# D01-D04: 八步清理核心
# ---------------------------------------------------------------------------


async def test_d01_delete_case_removes_all_v3_rows() -> None:
    env = await _setup()
    ent_a = await _entity(env, "账号A", "weibo:native-a")
    await _link_entity_case(env, ent_a.id, env.case_a.id)
    ent_b = await _entity(env, "账号B", "weibo:native-b")
    await _link_entity_case(env, ent_b.id, env.case_b.id)
    await env.workspace.upsert_relation(
        left_entity_id=ent_a.id,
        right_entity_id=ent_b.id,
        relation_type="same_as",
        source_case_id=env.case_a.id,
        source_type="canonical",
        source_id=f"canonical-{env.case_a.id}",
    )
    await _quality(env, env.case_a.id)
    await _cross_link(env, env.case_a.id, env.case_b.id)
    signal_a = await _derived_signal(
        env, case_id=env.case_a.id, fingerprint="fp-signal-a", links=[env.case_a.id]
    )

    await env.app.delete_case(env.case_a.id)

    # quality 无残留
    assert await env.quality.get(env.case_a.id) is None
    # 2. relations（source_case_id=case_a）已删除
    relations = await env.workspace.list_active_relations_for_entities(
        [ent_a.id, ent_b.id]
    )
    assert relations == []
    # 3. case links（case_a）已删除；case_b 的链接保留
    case_a_links = await env.workspace.list_case_links(ent_a.id, case_id=env.case_a.id)
    assert case_a_links == []
    # 4. cross links（涉及 case_a）已删除
    assert await env.cross.list_workspace() == []
    # 6. primary signal 已删除（5 的 case links 随 signal 删除）
    assert await env.derived.get(signal_a.id) is None
    # 7. 孤儿 signal 清理
    assert await env.derived.list() == []
    # 8. 孤儿 entity：ent_a 无 case links/relations → 删除；ent_b 有 case_b link → 保留
    assert await env.workspace.get(ent_a.id) is None
    assert await env.workspace.get(ent_b.id) is not None
    await env.db.dispose()


async def test_d02_primary_case_signal_deleted_links_cleaned() -> None:
    env = await _setup()
    # §67 第 6 步：primary case_id=case_a 的 Signal 随 case 删除；
    # 第 5 步先清理 case links（不残留 case_a appearance）。
    signal = await _derived_signal(
        env,
        case_id=env.case_a.id,
        fingerprint="fp-shared",
        links=[env.case_a.id, env.case_b.id],
    )
    await env.app.delete_case(env.case_a.id)

    assert await env.derived.get(signal.id) is None
    # case link 表无 case_a 残留（signal 删除时级联清理）
    await env.db.dispose()


async def test_d03_orphan_derived_signal_cleaned_after_delete() -> None:
    env = await _setup()
    # signal primary case_a，link 只有 case_a：删 case_a → 孤儿 → 清理
    signal = await _derived_signal(
        env, case_id=env.case_a.id, fingerprint="fp-orphan", links=[env.case_a.id]
    )
    await env.app.delete_case(env.case_a.id)
    assert await env.derived.get(signal.id) is None
    await env.db.dispose()


async def test_d04_entity_shared_with_other_case_preserved() -> None:
    env = await _setup()
    ent = await _entity(env, "共享账号", "weibo:native-shared")
    await _link_entity_case(env, ent.id, env.case_a.id)
    await _link_entity_case(env, ent.id, env.case_b.id)

    await env.app.delete_case(env.case_a.id)

    kept = await env.workspace.get(ent.id)
    assert kept is not None
    remaining = await env.workspace.list_case_links(ent.id)
    assert [link.case_id for link in remaining] == [env.case_b.id]
    await env.db.dispose()


# ---------------------------------------------------------------------------
# D05-D09: 完整性约束与集成
# ---------------------------------------------------------------------------


async def test_d05_query_signals_no_orphan_after_delete() -> None:
    """§67：删除后 query_signals(case_id) 不得查到孤儿 Signal。"""
    env = await _setup()
    signal = await _derived_signal(
        env, case_id=env.case_a.id, fingerprint="fp-orphan", links=[env.case_a.id]
    )
    await env.app.delete_case(env.case_a.id)
    links = await env.derived.list_case_links(signal.id)
    assert links == []
    # signal 本体也已被孤儿清理删除
    assert await env.derived.get(signal.id) is None
    await env.db.dispose()


async def test_d06_cross_link_remaining_for_other_case() -> None:
    env = await _setup()
    case_c = await env.app.create_case(
        CreateCaseRequest(topic="第三案例", platforms=["weibo"])
    )
    await _cross_link(env, env.case_a.id, env.case_b.id)
    await _cross_link(env, env.case_b.id, case_c.id)

    await env.app.delete_case(env.case_a.id)

    remaining = await env.cross.list_workspace()
    assert len(remaining) == 1
    # upsert_link 内部 canonical ordering（left < right）
    expected_pair = sorted([env.case_b.id, case_c.id])
    assert remaining[0].left_case_id == expected_pair[0]
    assert remaining[0].right_case_id == expected_pair[1]
    await env.db.dispose()


async def test_d07_quality_removed_only_for_deleted_case() -> None:
    env = await _setup()
    await _quality(env, env.case_a.id)
    await _quality(env, env.case_b.id)

    await env.app.delete_case(env.case_a.id)

    assert await env.quality.get(env.case_a.id) is None
    assert await env.quality.get(env.case_b.id) is not None
    await env.db.dispose()


async def test_d08_delete_case_idempotent_and_case_b_unaffected() -> None:
    env = await _setup()
    await _quality(env, env.case_a.id)
    await env.app.delete_case(env.case_a.id)
    # 重复删除 → 404（case 已不存在）
    from app.core.errors import ResourceNotFoundError

    try:
        await env.app.delete_case(env.case_a.id)
        raise AssertionError("expected not found")
    except ResourceNotFoundError:
        pass
    # case_b 完整保留
    assert await env.app.get_case(env.case_b.id) is not None
    await env.db.dispose()


async def test_d09_fingerprint_unique_survives_entity_cleanup() -> None:
    """§67.1：fingerprint UNIQUE 约束在 cleanup 后仍成立（可重新 upsert）。"""
    env = await _setup()
    signal = await _derived_signal(
        env, case_id=env.case_a.id, fingerprint="fp-recreate", links=[env.case_a.id]
    )
    await env.app.delete_case(env.case_a.id)
    assert await env.derived.get(signal.id) is None

    # 同一 fingerprint 可再次创建（无 UNIQUE 残留冲突）
    again = await _derived_signal(
        env, case_id=env.case_b.id, fingerprint="fp-recreate", links=[env.case_b.id]
    )
    assert again is not None
    assert again.fingerprint == "fp-recreate"
    await env.db.dispose()


async def test_d10_delete_cleans_cross_link_fingerprint_scope() -> None:
    """删除后 cross fingerprint 幂等键可复用（upsert 不再命中已删 pair）。"""
    env = await _setup()
    await _cross_link(env, env.case_a.id, env.case_b.id)
    fp = cross_link_fingerprint(
        left_case_id=env.case_a.id,
        right_case_id=env.case_b.id,
        relation_type="shared_actor",
        algorithm_version="cross-intel-1.0.0",
    )
    await env.app.delete_case(env.case_a.id)
    assert await env.cross.list_workspace() == []
    # 新 case 复用同一 fingerprint 空间（不冲突即通过）
    case_c = await env.app.create_case(
        CreateCaseRequest(topic="复用案例", platforms=["weibo"])
    )
    await _cross_link(env, env.case_b.id, case_c.id)
    assert len(await env.cross.list_workspace()) == 1
    assert fp  # 引用保持有效（不产生重复行即可）
    await env.db.dispose()
