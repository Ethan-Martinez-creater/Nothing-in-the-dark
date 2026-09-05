"""V3 Approval Rework: Cross → Signal 真实链路集成测试（CS01-CS06）。

全部使用真实 CrossInvestigationService / CrossInvestigationRepository /
AdvancedSignalDetectorService / DerivedSignalRepository /
WorkspaceEntityService（内存 SQLite），不手工构造 fake evidence contract。
覆盖 Rework R2（真实 overlap contract）、R3（identity component 传播）、
R4（global reconcile）与 E2E-F 的 retract 传播（CS06）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete

from app.application.advanced_signal_service import AdvancedSignalDetectorService
from app.application.cross_investigation_service import CrossInvestigationService
from app.application.repositories import ApplicationRepository
from app.application.workspace_entity_service import WorkspaceEntityService
from app.infrastructure.database.alignment_repository import AlignmentRepository
from app.infrastructure.database.cross_investigation_repository import (
    CrossInvestigationRepository,
)
from app.infrastructure.database.derived_signal_repository import (
    DerivedSignalRepository,
)
from app.infrastructure.database.integrity_repository import IntegrityRepository
from app.infrastructure.database.media_pipeline_repository import (
    MediaPipelineRepository,
)
from app.infrastructure.database.models import (
    AccountRecord,
    MediaAssetRecord,
    SourcePostRecord,
)
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.database.workspace_entity_repository import (
    WorkspaceEntityRepository,
)
from app.schemas.cases import CreateCaseRequest
from tests.memory_db import MemoryDatabase


async def _setup(*, with_case_c: bool = False) -> Any:
    database = MemoryDatabase()
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    workspace_repo = WorkspaceEntityRepository(database)
    social_repo = SocialRepository(database)
    media_repo = MediaPipelineRepository(database)
    integrity_repo = IntegrityRepository(database)
    alignment_repo = AlignmentRepository(database)
    workspace_service = WorkspaceEntityService(
        workspace_repository=workspace_repo,
        alignment_repository=alignment_repo,
        application_repository=app_repo,
        social_repository=social_repo,
        integrity_repository=integrity_repo,
        database=database,
    )
    cross_repo = CrossInvestigationRepository(database)
    service = CrossInvestigationService(
        cross_repository=cross_repo,
        workspace_repository=workspace_repo,
        workspace_service=workspace_service,
        social_repository=social_repo,
        media_repository=media_repo,
        application_repository=app_repo,
        database=database,
    )
    advanced = AdvancedSignalDetectorService(
        derived_repository=DerivedSignalRepository(database),
        integrity_repository=integrity_repo,
        analysis_job_repository=SimpleNamespace(),  # coordination 专用，CS 不触
        workspace_service=workspace_service,
        cross_repository=cross_repo,
        media_repository=media_repo,
        application_repository=app_repo,
    )
    case_a = await app_repo.create_case(
        CreateCaseRequest(topic="CS 案A", platforms=["weibo"])
    )
    case_b = await app_repo.create_case(
        CreateCaseRequest(topic="CS 案B", platforms=["weibo"])
    )
    case_c = None
    if with_case_c:
        case_c = await app_repo.create_case(
            CreateCaseRequest(topic="CS 案C", platforms=["weibo"])
        )
    return SimpleNamespace(
        db=database,
        app=app_repo,
        workspace=workspace_repo,
        social=social_repo,
        media=media_repo,
        integrity=integrity_repo,
        alignment=alignment_repo,
        workspace_service=workspace_service,
        cross=cross_repo,
        service=service,
        advanced=advanced,
        derived=DerivedSignalRepository(database),
        case_a=case_a,
        case_b=case_b,
        case_c=case_c,
    )


def _post(
    platform: str,
    native_id: str,
    author_id: str,
    *,
    content: str = "事件讨论内容",
) -> dict[str, Any]:
    return {
        "platform": platform,
        "native_id": native_id,
        "content_type": "post",
        "title": "",
        "content": content,
        "author": f"author-{author_id}",
        "published_at": "2026-08-15T10:00:00+08:00",
        "engagement": 5,
        "metrics": {"total": 5},
        "url": f"https://example.com/{platform}/{native_id}",
        "raw": {"id": native_id, "user_id": author_id},
        "comments": [],
    }


async def _account(
    env: Any, case_id: str, platform: str, native_id: str, name: str
) -> Any:
    return await env.app.upsert_account(
        case_id=case_id,
        platform=platform,
        native_id=native_id,
        name=name,
        normalized_name=name,
    )


async def _asset(
    env: Any,
    case_id: str,
    *,
    sha256: str | None = None,
    phash: str | None = None,
) -> MediaAssetRecord:
    record = MediaAssetRecord(
        case_id=case_id,
        post_id=None,
        platform="weibo",
        media_type="image",
        url=f"https://example.com/media/{sha256 or phash}-{case_id[:4]}",
        normalized_url=f"https://example.com/media/{sha256 or phash}",
        file_sha256=sha256,
        actual_sha256=sha256,
        phash=phash,
    )
    async with env.db.session_factory() as session:
        session.add(record)
        await session.commit()
        await session.refresh(record)
    return record


def _pair(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a, b)))  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# CS01: 真实 overlap warning（Rework R2 contract）
# ---------------------------------------------------------------------------


async def test_cs01_real_overlap_warning_from_observed_links() -> None:
    env = await _setup()
    # 3 个 shared actors：Case A 账号 + Case B 帖子作者（AccountRecord 全局唯一）
    for index in (1, 2, 3):
        await _account(env, env.case_a.id, "weibo", f"s{index}", f"主体s{index}")
        await env.social.persist_batch(
            case_id=env.case_b.id,
            posts=[_post("weibo", f"b{index}", f"s{index}")],
        )
    # 2 个 exact shared media
    await _asset(env, env.case_a.id, sha256="c1" * 32)
    await _asset(env, env.case_b.id, sha256="c1" * 32)
    await _asset(env, env.case_a.id, sha256="c2" * 32)
    await _asset(env, env.case_b.id, sha256="c2" * 32)
    await env.workspace_service.refresh_case(env.case_a.id)
    await env.workspace_service.refresh_case(env.case_b.id)
    summary = await env.service.refresh_case(env.case_a.id)
    assert summary["shared_actor"]["upserted"] >= 1
    assert summary["shared_media"]["upserted"] >= 1

    left, right = _pair(env.case_a.id, env.case_b.id)
    pair_links = await env.cross.list_between(left, right)
    link_counts = {
        link.relation_type: link.evidence_count for link in pair_links
    }
    assert link_counts["shared_actor"] == 3
    assert link_counts["shared_media"] == 2

    await env.advanced.refresh_cross_case_overlap()
    signals = await env.derived.list()
    overlap = [
        s for s in signals if s.signal_type == "cross_case_overlap"
    ]
    assert len(overlap) == 1
    assert overlap[0].severity == "warning"
    assert overlap[0].metric_snapshot_json["overlap_score"] == pytest.approx(0.70)
    assert sorted(overlap[0].related_case_ids_json) == [left, right]
    await env.db.dispose()


# ---------------------------------------------------------------------------
# CS02: candidate media 不进入 overlap（Rework R2）
# ---------------------------------------------------------------------------


async def test_cs02_candidate_media_excluded_from_overlap() -> None:
    env = await _setup()
    await _account(env, env.case_a.id, "weibo", "only", "唯一主体")
    await env.social.persist_batch(
        case_id=env.case_b.id, posts=[_post("weibo", "b1", "only")]
    )
    # phash-only candidate（无 exact SHA）
    await _asset(env, env.case_a.id, phash="e" * 64)
    await _asset(env, env.case_b.id, phash="e" * 64)
    await env.workspace_service.refresh_case(env.case_a.id)
    await env.workspace_service.refresh_case(env.case_b.id)
    await env.service.refresh_case(env.case_a.id)

    # shared_media candidate link 已生成
    left, right = _pair(env.case_a.id, env.case_b.id)
    media_links = [
        link
        for link in await env.cross.list_between(left, right)
        if link.relation_type == "shared_media"
    ]
    assert len(media_links) == 1
    assert media_links[0].status == "candidate"

    # observed relation types 只有 shared_actor 1 种 → 无 overlap signal
    summary = await env.advanced.refresh_cross_case_overlap()
    assert summary["upserted"] == 0
    assert await env.derived.list() == []
    await env.db.dispose()


# ---------------------------------------------------------------------------
# CS03: observed media 不被 candidate 降级（Rework R5）
# ---------------------------------------------------------------------------


async def test_cs03_observed_media_not_downgraded_by_candidate() -> None:
    env = await _setup()
    # 同 Pair：一个 exact SHA + 一个 phash candidate
    await _asset(env, env.case_a.id, sha256="c3" * 32)
    await _asset(env, env.case_b.id, sha256="c3" * 32)
    await _asset(env, env.case_a.id, phash="e" * 64)
    await _asset(env, env.case_b.id, phash="e" * 64)
    await env.service.refresh_case(env.case_a.id)

    left, right = _pair(env.case_a.id, env.case_b.id)
    media_links = [
        link
        for link in await env.cross.list_between(left, right)
        if link.relation_type == "shared_media" and link.is_active
    ]
    # 禁止同 Pair 两条 shared_media payload；observed 必须胜出
    assert len(media_links) == 1
    assert media_links[0].status == "observed"
    assert media_links[0].score == 1.0
    assert media_links[0].evidence_count == 1
    await env.db.dispose()


# ---------------------------------------------------------------------------
# CS04/CS05: subject 完全消失 → global reconcile（Rework R4）
# ---------------------------------------------------------------------------


async def test_cs04_actor_signal_resolved_after_subject_disappears() -> None:
    env = await _setup(with_case_c=True)
    await _account(env, env.case_a.id, "weibo", "rec", "复现主体")
    for case in (env.case_b, env.case_c):
        await env.social.persist_batch(
            case_id=case.id, posts=[_post("weibo", f"p-{case.id[:4]}", "rec")]
        )
    for case in (env.case_a, env.case_b, env.case_c):
        await env.workspace_service.refresh_case(case.id)
    await env.advanced.refresh_actor_recurrence()
    signals = await env.derived.list()
    assert len(signals) == 1
    assert signals[0].signal_type == "actor_recurrence"
    assert signals[0].status == "open"
    assert signals[0].detector_active is True

    # 主体彻底消失：删账号 + 删全部帖子，再 refresh 让 case links 收敛
    async with env.db.session_factory() as session:
        await session.execute(delete(SourcePostRecord))
        await session.execute(
            delete(AccountRecord).where(AccountRecord.platform == "weibo")
        )
        await session.commit()
    for case in (env.case_a, env.case_b, env.case_c):
        await env.workspace_service.refresh_case(case.id)
    summary = await env.advanced.refresh_actor_recurrence()
    assert summary["upserted"] == 0
    assert summary["stale_deactivated"] == 1
    signals = await env.derived.list()
    assert len(signals) == 1
    assert signals[0].detector_active is False
    assert signals[0].status == "resolved"
    await env.db.dispose()


async def test_cs05_media_signal_resolved_after_assets_removed() -> None:
    env = await _setup()
    sha = "dd" * 32
    await _asset(env, env.case_a.id, sha256=sha)
    await _asset(env, env.case_b.id, sha256=sha)
    await env.advanced.refresh_media_reuse()
    signals = await env.derived.list()
    assert len(signals) == 1
    assert signals[0].signal_type == "media_reuse"
    assert signals[0].status == "open"
    assert signals[0].detector_active is True

    async with env.db.session_factory() as session:
        await session.execute(
            delete(MediaAssetRecord).where(MediaAssetRecord.actual_sha256 == sha)
        )
        await session.commit()
    summary = await env.advanced.refresh_media_reuse()
    assert summary["upserted"] == 0
    assert summary["stale_deactivated"] == 1
    signals = await env.derived.list()
    assert signals[0].detector_active is False
    assert signals[0].status == "resolved"
    await env.db.dispose()


# ---------------------------------------------------------------------------
# CS06: 跨平台 Identity Component 传播 + retract（补齐 E2E-F）
# ---------------------------------------------------------------------------


async def test_cs06_cross_platform_component_propagates_and_retracts() -> None:
    env = await _setup(with_case_c=True)
    await _account(env, env.case_a.id, "weibo", "X", "主体X")
    await _account(env, env.case_b.id, "bilibili", "Y", "主体Y")
    # Case C：X / Y 以帖子作者出现，canonical entity 的 mentions 将其
    # materialize 为同一 identity → same_as relation
    await env.social.persist_batch(
        case_id=env.case_c.id,
        posts=[
            _post("weibo", "cp1", "X"),
            _post("bilibili", "cp2", "Y"),
        ],
    )
    await env.workspace_service.refresh_case(env.case_a.id)
    await env.workspace_service.refresh_case(env.case_b.id)
    await env.workspace_service.refresh_case(env.case_c.id)

    # X 的 workspace entity（weibo:X 的 deterministic key）
    x_entity = await env.workspace.find_by_key("platform_account", "weibo:X")
    y_entity = await env.workspace.find_by_key("platform_account", "bilibili:Y")
    assert x_entity is not None and y_entity is not None

    canonical = await env.alignment.upsert_canonical_entity(
        case_id=env.case_c.id,
        entity_type="account",
        canonical_name="跨平台主体XY",
    )
    for object_id in (
        "post-author:weibo:X",
        "post-author:bilibili:Y",
    ):
        await env.alignment.create_entity_mention(
            case_id=env.case_c.id,
            entity_id=canonical.id,
            platform_object_type="account",
            platform_object_id=object_id,
        )
    await env.workspace_service.refresh_case(env.case_c.id)
    component = await env.workspace_service.identity_component(x_entity.id)
    assert y_entity.id in component["entity_ids"]

    # 传播后：cross refresh A → A-B shared_actor observed
    await env.service.refresh_case(env.case_a.id)
    left, right = _pair(env.case_a.id, env.case_b.id)
    ab_links = [
        link
        for link in await env.cross.list_between(left, right)
        if link.relation_type == "shared_actor"
    ]
    assert len(ab_links) == 1
    assert ab_links[0].status == "observed"

    # retract Case C materialization → relation retracted → link inactive
    async with env.db.session_factory() as session:
        from app.infrastructure.database.models import (
            CanonicalEntityRecord,
            EntityMentionRecord,
        )

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
    result = await env.workspace_service.refresh_case(env.case_c.id)
    assert result["relations_retracted"] >= 1
    await env.service.refresh_case(env.case_a.id)
    ab_links = [
        link
        for link in await env.cross.list_between(left, right)
        if link.relation_type == "shared_actor" and link.is_active
    ]
    assert ab_links == []
    await env.db.dispose()
