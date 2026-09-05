"""V3 Final Closure FC1: detector 分页完整性 / safety cap / reconcile 守卫。

核心断言（FC1-T01~T10）：超过原 hard limit 后不误清理仍有效的 Signal /
Link；scan_complete=False 时绝不执行 destructive stale reconciliation。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.application import advanced_signal_service as adv_module
from app.application.advanced_signal_service import (
    AdvancedSignalDetectorService,
    _fingerprint,
)
from app.application.cross_investigation_service import CrossInvestigationService
from app.application.repositories import ApplicationRepository
from app.application.workspace_entity_service import WorkspaceEntityService
from app.infrastructure.database.alignment_repository import AlignmentRepository
from app.infrastructure.database.cross_investigation_repository import (
    CrossInvestigationRepository,
    cross_link_fingerprint,
)
from app.infrastructure.database.derived_signal_repository import (
    DerivedSignalRepository,
)
from app.infrastructure.database.integrity_repository import IntegrityRepository
from app.infrastructure.database.media_pipeline_repository import (
    MediaPipelineRepository,
)
from app.infrastructure.database.models import (
    CrossInvestigationLinkRecord,
    MediaAssetRecord,
    WorkspaceEntityCaseLinkRecord,
    WorkspaceEntityRecord,
)
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.database.workspace_entity_repository import (
    WorkspaceEntityRepository,
)
from app.schemas.cases import CreateCaseRequest
from tests.memory_db import MemoryDatabase

SIGNAL_BASE: dict[str, Any] = {
    "source_type": "derived",
    "source_id": "subj-1",
    "signal_type": "actor_recurrence",
    "severity": "warning",
    "title": "t",
    "why_it_matters": "w",
    "confidence": None,
    "metric_snapshot": {},
    "evidence_refs": [],
    "related_case_ids": ["case-a"],
    "detector_version": "advanced-signal-1.0.0",
}


def _async_return(value: Any) -> Any:
    async def _inner(*args: Any, **kwargs: Any) -> Any:
        return value

    return _inner


async def _setup() -> SimpleNamespace:
    database = MemoryDatabase()
    await database.create_schema()
    derived = DerivedSignalRepository(database)
    return SimpleNamespace(db=database, derived=derived)


def _payload(**overrides: Any) -> dict[str, Any]:
    payload = {**SIGNAL_BASE, "fingerprint": "fp-x"}
    payload.update(overrides)
    payload.setdefault("case_id", "case-a")
    return payload


def _make_detector(env: SimpleNamespace, **deps: Any) -> AdvancedSignalDetectorService:
    return AdvancedSignalDetectorService(
        derived_repository=env.derived,
        integrity_repository=deps.get("integrity"),
        analysis_job_repository=deps.get("jobs"),
        workspace_service=deps.get("workspace"),
        cross_repository=deps.get("cross"),
        media_repository=deps.get("media"),
        application_repository=deps.get("app_repo"),
    )


def _cross_link(left: str, right: str, etype: str, count: int) -> SimpleNamespace:
    """真实 Cross Link contract 的轻量 stub（带 keyset cursor 字段）。"""
    return SimpleNamespace(
        id=f"link-{left}-{right}-{etype}",
        left_case_id=left,
        right_case_id=right,
        relation_type=etype,
        is_active=True,
        status="observed",
        score=1.0,
        evidence_count=count,
        evidence_refs_json=[],
        updated_at=datetime.now(UTC),
    )


async def _seed_cross_link(
    env: SimpleNamespace,
    *,
    link_id: str,
    left: str,
    right: str,
    relation_type: str,
    fingerprint: str,
    updated_at: datetime,
    evidence_count: int = 1,
) -> None:
    """直接落一条 active observed Cross Link（构造超限数据集；
    fingerprint 语义由调用方保证）。"""
    async with env.db.session_factory() as session:
        session.add(
            CrossInvestigationLinkRecord(
                id=link_id,
                left_case_id=left,
                right_case_id=right,
                relation_type=relation_type,
                status="observed",
                score=1.0,
                evidence_count=evidence_count,
                evidence_refs_json=[],
                feature_scores_json={},
                fingerprint=fingerprint,
                algorithm_version="cross-intel-1.0.0",
                is_active=True,
                updated_at=updated_at,
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# FC1-T01/T02/T09/T10: cross_case_overlap 完整性
# ---------------------------------------------------------------------------


async def test_fc1_t01_overlap_beyond_200_links_keeps_valid_signal() -> None:
    """202 条 observed links（> 旧 limit=200）分页全量扫描；位于旧窗口
    之外（updated_at 最旧）的有效 overlap Signal 保持 active。"""
    env = await _setup()
    anchor = "case-anchor"
    target_left, target_right = "case-t-left", "case-t-right"
    for relation_type, count in (("shared_actor", 3), ("shared_media", 2)):
        await _seed_cross_link(
            env,
            link_id=f"link-target-{relation_type}",
            left=target_left,
            right=target_right,
            relation_type=relation_type,
            fingerprint=cross_link_fingerprint(
                left_case_id=target_left,
                right_case_id=target_right,
                relation_type=relation_type,
                algorithm_version="cross-intel-1.0.0",
            ),
            updated_at=datetime(2020, 1, 1, tzinfo=UTC),  # 最旧 → 旧窗口外
            evidence_count=count,
        )
    target_fp = _fingerprint(
        "cross_case_overlap",
        target_left,
        target_right,
        "advanced-signal-1.0.0",
    )
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id=f"{target_left}:{target_right}",
            fingerprint=target_fp,
            signal_type="cross_case_overlap",
            related_case_ids=[target_left, target_right],
        )
    )
    now = datetime.now(UTC)
    for index in range(200):
        await _seed_cross_link(
            env,
            link_id=f"link-filler-{index:04d}",
            left=anchor,
            right=f"case-filler-{index:04d}",
            relation_type="shared_post",
            fingerprint=f"fp-filler-{index:04d}",
            updated_at=now + timedelta(milliseconds=index),
        )

    detector = _make_detector(env, cross=CrossInvestigationRepository(env.db))
    summary = await detector.refresh_cross_case_overlap()
    assert summary["scan_complete"] is True
    target = [
        r for r in await env.derived.list() if r.fingerprint == target_fp
    ]
    assert len(target) == 1
    assert target[0].detector_active is True  # 不被误判 stale
    assert target[0].status == "open"
    await env.db.dispose()


async def test_fc1_t02_cross_scan_safety_cap_skips_reconcile(monkeypatch) -> None:
    """cap=10、rows=11 → scan_complete=False、stale_deactivated=0、
    旧 active Signal 不得 resolved。"""
    monkeypatch.setattr(adv_module, "MAX_CROSS_LINK_SCAN", 10)
    env = await _setup()
    stale_fp = _fingerprint(
        "cross_case_overlap", "case-x", "case-y", "advanced-signal-1.0.0"
    )
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="case-x:case-y",
            fingerprint=stale_fp,
            signal_type="cross_case_overlap",
        )
    )
    links = [_cross_link("case-a", f"case-b{index:02d}", "shared_post", 1)
             for index in range(11)]
    detector = _make_detector(
        env, cross=SimpleNamespace(list_workspace_detector_page=_async_return(links))
    )
    summary = await detector.refresh_cross_case_overlap()
    assert summary["scan_complete"] is False
    assert summary["stale_deactivated"] == 0
    record = (await env.derived.list())[0]
    assert record.fingerprint == stale_fp
    assert record.detector_active is True  # fail-safe：不误清理
    assert record.status == "open"
    await env.db.dispose()


async def test_fc1_t09_cross_link_pagination_deterministic() -> None:
    """same updated_at + 多 id：keyset cursor (updated_at, id) 无重复无漏项。"""
    env = await _setup()
    same_ts = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
    ids = []
    for index in range(5):
        link_id = f"link-{index:04d}"
        ids.append(link_id)
        await _seed_cross_link(
            env,
            link_id=link_id,
            left="case-a",
            right=f"case-b{index:02d}",
            relation_type="shared_post",
            fingerprint=f"fp-det-{index:04d}",
            updated_at=same_ts if index < 3 else same_ts + timedelta(seconds=index),
        )
    repo = CrossInvestigationRepository(env.db)
    collected: list[str] = []
    after_updated_at: datetime | None = None
    after_id: str | None = None
    while True:
        rows = await repo.list_workspace_detector_page(
            status="observed",
            after_updated_at=after_updated_at,
            after_id=after_id,
            limit=2,
        )
        if not rows:
            break
        assert len(rows) <= 2
        collected.extend(str(row.id) for row in rows)
        after_updated_at = rows[-1].updated_at
        after_id = str(rows[-1].id)
    assert sorted(collected) == sorted(ids)
    assert len(collected) == len(set(collected))
    await env.db.dispose()


async def test_fc1_t10_batch_failure_keeps_signals_active() -> None:
    """第 2 页 repository 抛异常 → detector 抛异常（job 可 retry），但
    stale reconciliation 未执行、旧 Signal 保持 active。"""
    env = await _setup()
    stale_fp = _fingerprint(
        "cross_case_overlap", "case-x", "case-y", "advanced-signal-1.0.0"
    )
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="case-x:case-y",
            fingerprint=stale_fp,
            signal_type="cross_case_overlap",
        )
    )
    first_page = [
        _cross_link("case-a", f"case-b{index:03d}", "shared_post", 1)
        for index in range(500)  # 满页（page_size=500）→ 触发第二页调用
    ]

    class _FailingCrossRepo:
        def __init__(self) -> None:
            self.calls = 0

        async def list_workspace_detector_page(self, **kwargs: Any) -> list[Any]:
            self.calls += 1
            if self.calls == 1:
                return first_page
            raise RuntimeError("cursor page failure")

    detector = _make_detector(env, cross=_FailingCrossRepo())
    with pytest.raises(RuntimeError):
        await detector.refresh_cross_case_overlap()
    record = (await env.derived.list())[0]
    assert record.fingerprint == stale_fp
    assert record.detector_active is True
    assert record.status == "open"
    await env.db.dispose()


# ---------------------------------------------------------------------------
# FC1-T03: actor_recurrence entity scan boundary
# ---------------------------------------------------------------------------


async def _seed_entities(env: SimpleNamespace, count: int, case_id: str) -> None:
    async with env.db.session_factory() as session:
        for index in range(count):
            entity = WorkspaceEntityRecord(
                id=f"ent-{index:02d}",
                entity_type="account",
                canonical_name=f"主体{index}",
                status="active",
            )
            session.add(entity)
            session.add(
                WorkspaceEntityCaseLinkRecord(
                    entity_id=entity.id,
                    case_id=case_id,
                    source_type="account",
                    source_id=f"acc-{index}",
                    first_seen_at=datetime.now(UTC),
                    last_seen_at=datetime.now(UTC),
                )
            )
        await session.commit()


async def test_fc1_t03_actor_entity_scan_boundary(monkeypatch) -> None:
    """cap=5 / entities=6 → incomplete → no global reconcile；6 entities /
    page_size=2 多页聚合 → complete=True 且 component 正确。"""
    monkeypatch.setattr(adv_module, "MAX_ACTOR_ENTITY_SCAN", 5)
    env = await _setup()
    await _seed_entities(env, 6, "case-a")
    workspace = WorkspaceEntityService(
        workspace_repository=WorkspaceEntityRepository(env.db),
        alignment_repository=AlignmentRepository(env.db),
        application_repository=SimpleNamespace(),
        social_repository=SocialRepository(env.db),
        integrity_repository=IntegrityRepository(env.db),
        database=env.db,
    )
    # cap=5 → incomplete
    components, complete = await workspace.list_components_with_cases_complete(
        max_entities=5
    )
    assert complete is False
    assert len(components) == 5
    # 多页完整：page_size=2 → 3 页聚合
    components, complete = await workspace.list_components_with_cases_complete(
        max_entities=50, page_size=2
    )
    assert complete is True
    assert len(components) == 6  # 6 个孤立实体 → 6 个单实体 component

    # refresh 全链：cap=5 → 旧 active signal 不被 resolve
    stale_fp = _fingerprint(
        "actor_recurrence", "ent-00", "advanced-signal-1.0.0"
    )
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="ent-00",
            fingerprint=stale_fp,
            signal_type="actor_recurrence",
        )
    )
    detector = _make_detector(env, workspace=workspace)
    summary = await detector.refresh_actor_recurrence()
    assert summary["scan_complete"] is False
    assert summary["stale_deactivated"] == 0
    stale = [r for r in await env.derived.list() if r.fingerprint == stale_fp]
    assert stale[0].detector_active is True
    assert stale[0].status == "open"
    await env.db.dispose()


# ---------------------------------------------------------------------------
# FC1-T04: media_reuse SHA boundary + 分页
# ---------------------------------------------------------------------------


async def test_fc1_t04_media_sha_scan_boundary_and_pagination(monkeypatch) -> None:
    """cap=10 / rows=11 → incomplete → no reconcile；真实 repo SHA keyset
    分页（page=2）无重复无漏项。"""
    monkeypatch.setattr(adv_module, "MAX_MEDIA_REUSE_SHA_SCAN", 10)
    env = await _setup()
    stale_fp = _fingerprint("media_reuse", "sha-stale", "advanced-signal-1.0.0")
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="sha-stale",
            fingerprint=stale_fp,
            signal_type="media_reuse",
        )
    )
    rows = [
        {
            "sha256": f"sha-{index:02d}",
            "case_count": 2,
            "case_ids": ["case-a", f"case-b{index:02d}"],
        }
        for index in range(11)
    ]
    detector = _make_detector(
        env, media=SimpleNamespace(list_sha_case_counts_page=_async_return(rows))
    )
    summary = await detector.refresh_media_reuse()
    assert summary["scan_complete"] is False
    assert summary["stale_deactivated"] == 0
    stale = [r for r in await env.derived.list() if r.fingerprint == stale_fp]
    assert stale[0].detector_active is True
    assert stale[0].status == "open"
    await env.db.dispose()

    # 真实 repo：4 个 SHA 的 keyset 分页（page=2 → 2 页）
    env2 = await _setup()
    media_repo = MediaPipelineRepository(env2.db)
    for index in range(4):
        sha = f"{index:02d}" * 32
        for case_id in ("case-a", "case-b"):
            async with env2.db.session_factory() as session:
                session.add(
                    MediaAssetRecord(
                        case_id=case_id,
                        platform="weibo",
                        media_type="image",
                        url=f"https://example.com/{sha}-{case_id}",
                        normalized_url=f"https://example.com/{sha}",
                        actual_sha256=sha,
                    )
                )
                await session.commit()
    collected: list[str] = []
    after: str | None = None
    while True:
        page = await media_repo.list_sha_case_counts_page(after_sha=after, limit=2)
        if not page:
            break
        collected.extend(row["sha256"] for row in page)
        after = page[-1]["sha256"]
    assert sorted(collected) == sorted(f"{index:02d}" * 32 for index in range(4))
    assert len(collected) == len(set(collected))
    await env2.db.dispose()


# ---------------------------------------------------------------------------
# FC1-T05/T06/T07/T08: case-scoped cross detector partial scan
# ---------------------------------------------------------------------------


async def _setup_cross() -> SimpleNamespace:
    database = MemoryDatabase()
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    workspace_repo = WorkspaceEntityRepository(database)
    social_repo = SocialRepository(database)
    media_repo = MediaPipelineRepository(database)
    integrity_repo = IntegrityRepository(database)
    workspace_service = WorkspaceEntityService(
        workspace_repository=workspace_repo,
        alignment_repository=AlignmentRepository(database),
        application_repository=app_repo,
        social_repository=social_repo,
        integrity_repository=integrity_repo,
        database=database,
    )
    service = CrossInvestigationService(
        cross_repository=CrossInvestigationRepository(database),
        workspace_repository=workspace_repo,
        workspace_service=workspace_service,
        social_repository=social_repo,
        media_repository=media_repo,
        application_repository=app_repo,
        database=database,
    )
    case_a = await app_repo.create_case(
        CreateCaseRequest(topic="FC 案A", platforms=["weibo"])
    )
    case_b = await app_repo.create_case(
        CreateCaseRequest(topic="FC 案B", platforms=["weibo"])
    )
    return SimpleNamespace(
        db=database,
        app=app_repo,
        workspace=workspace_repo,
        workspace_service=workspace_service,
        social=social_repo,
        media=media_repo,
        cross=CrossInvestigationRepository(database),
        service=service,
        case_a=case_a,
        case_b=case_b,
    )


def _post(platform: str, native_id: str, author_id: str) -> dict[str, Any]:
    return {
        "platform": platform,
        "native_id": native_id,
        "content_type": "post",
        "title": "",
        "content": "FC 事件讨论",
        "author": f"author-{author_id}",
        "published_at": "2026-08-15T10:00:00+08:00",
        "engagement": 5,
        "metrics": {"total": 5},
        "url": f"https://example.com/{platform}/{native_id}",
        "raw": {"id": native_id, "user_id": author_id},
        "comments": [],
    }


async def _seed_active_shared_link(
    env: SimpleNamespace,
    relation_type: str,
    left: str,
    right: str,
) -> str:
    fingerprint = cross_link_fingerprint(
        left_case_id=left,
        right_case_id=right,
        relation_type=relation_type,
        algorithm_version="cross-intel-1.0.0",
    )
    async with env.db.session_factory() as session:
        session.add(
            CrossInvestigationLinkRecord(
                id=f"seed-{relation_type}",
                left_case_id=left,
                right_case_id=right,
                relation_type=relation_type,
                status="observed",
                score=1.0,
                evidence_count=1,
                evidence_refs_json=[],
                feature_scores_json={},
                fingerprint=fingerprint,
                algorithm_version="cross-intel-1.0.0",
                is_active=True,
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    return fingerprint


async def test_fc1_t05_shared_post_partial_scan_keeps_old_link(monkeypatch) -> None:
    """FC1-T05：anchor posts 超过 test batch limit（批结果达到上限）→
    scan_complete=False → 不 reconcile_for_anchor，旧 link 保持 active。"""
    from app.application import cross_investigation_service as cross_module

    monkeypatch.setattr(cross_module, "_CROSS_MATCH_BATCH_LIMIT", 2)
    env = await _setup_cross()
    left, right = sorted((env.case_a.id, env.case_b.id))
    stale_fp = await _seed_active_shared_link(env, "shared_post", left, right)
    # 3 个 anchor posts + case B 相同 native_id 匹配（每批 2 pairs →
    # 2 rows == limit 2 → 可能截断）
    for index in range(3):
        await env.social.persist_batch(
            case_id=env.case_a.id,
            posts=[_post("weibo", f"n{index}", f"u{index}")],
        )
        await env.social.persist_batch(
            case_id=env.case_b.id,
            posts=[_post("weibo", f"n{index}", f"u-other{index}")],
        )
    summary = await env.service.refresh_case(env.case_a.id)
    assert summary["shared_post"]["scan_complete"] is False
    assert summary["shared_post"]["stale_deactivated"] == 0
    links = [
        link
        for link in await env.cross.list_between(left, right)
        if link.relation_type == "shared_post" and link.is_active
    ]
    assert [str(link.fingerprint) for link in links] == [stale_fp]
    await env.db.dispose()


async def test_fc1_t06_shared_content_partial_scan_keeps_old_link(monkeypatch) -> None:
    """FC1-T06：anchor hashes 超过 test batch limit → incomplete → 不
    reconcile_for_anchor。"""
    from app.application import cross_investigation_service as cross_module

    monkeypatch.setattr(cross_module, "_CROSS_MATCH_BATCH_LIMIT", 2)
    env = await _setup_cross()
    left, right = sorted((env.case_a.id, env.case_b.id))
    stale_fp = await _seed_active_shared_link(env, "shared_content", left, right)
    for index in range(3):
        await env.social.persist_batch(
            case_id=env.case_a.id,
            posts=[_post("weibo", f"c-a{index}", f"v{index}")],
        )
        await env.social.persist_batch(
            case_id=env.case_b.id,
            posts=[_post("weibo", f"c-b{index}", f"v{index}")],
        )
    summary = await env.service.refresh_case(env.case_a.id)
    assert summary["shared_content"]["scan_complete"] is False
    assert summary["shared_content"]["stale_deactivated"] == 0
    links = [
        link
        for link in await env.cross.list_between(left, right)
        if link.relation_type == "shared_content" and link.is_active
    ]
    assert [str(link.fingerprint) for link in links] == [stale_fp]
    await env.db.dispose()


async def test_fc1_t07_shared_media_partial_scan_keeps_observed(monkeypatch) -> None:
    """FC1-T07：完整分页时能发现 exact match；incomplete scan 时不删除
    旧 observed link。"""
    from app.application import cross_investigation_service as cross_module

    env = await _setup_cross()
    left, right = sorted((env.case_a.id, env.case_b.id))

    async def _asset(case_id: str, sha: str) -> None:
        async with env.db.session_factory() as session:
            session.add(
                MediaAssetRecord(
                    case_id=case_id,
                    platform="weibo",
                    media_type="image",
                    url=f"https://example.com/{sha}-{case_id[:4]}",
                    normalized_url=f"https://example.com/{sha}",
                    actual_sha256=sha,
                )
            )
            await session.commit()

    for index in range(3):
        sha = f"{index:02d}" * 32
        await _asset(env.case_a.id, sha)
        await _asset(env.case_b.id, sha)
    # 完整扫描：3 个 exact match 全部发现
    summary = await env.service.refresh_case(env.case_a.id)
    assert summary["shared_media"]["scan_complete"] is True
    media_links = [
        link
        for link in await env.cross.list_between(left, right)
        if link.relation_type == "shared_media" and link.is_active
    ]
    assert len(media_links) == 1
    assert media_links[0].status == "observed"
    assert media_links[0].evidence_count == 3
    stale_fp = str(media_links[0].fingerprint)

    # incomplete：batch limit=1 → 批结果达到上限 → 旧 observed link 不删除
    monkeypatch.setattr(cross_module, "_CROSS_MATCH_BATCH_LIMIT", 1)
    summary = await env.service.refresh_case(env.case_a.id)
    assert summary["shared_media"]["scan_complete"] is False
    links = [
        link
        for link in await env.cross.list_between(left, right)
        if link.relation_type == "shared_media" and link.is_active
    ]
    assert str(links[0].fingerprint) == stale_fp
    await env.db.dispose()


async def test_fc1_t08_shared_actor_partial_entity_scan(monkeypatch) -> None:
    """FC1-T08：Case entity 数超过 test cap → scan incomplete → 旧
    shared_actor link 保持 active。"""
    from app.application import cross_investigation_service as cross_module

    monkeypatch.setattr(cross_module, "MAX_CASE_ENTITY_SCAN", 2)
    env = await _setup_cross()
    left, right = sorted((env.case_a.id, env.case_b.id))
    stale_fp = await _seed_active_shared_link(env, "shared_actor", left, right)
    # 3 个共享 account → 3 个 workspace entity（> cap 2）
    for index in range(3):
        await env.app.upsert_account(
            case_id=env.case_a.id,
            platform="weibo",
            native_id=f"p{index}",
            name=f"主体{index}",
            normalized_name=f"主体{index}",
        )
        await env.social.persist_batch(
            case_id=env.case_b.id,
            posts=[_post("weibo", f"b{index}", f"p{index}")],
        )
    await env.workspace_service.refresh_case(env.case_a.id)
    await env.workspace_service.refresh_case(env.case_b.id)
    summary = await env.service.refresh_case(env.case_a.id)
    assert summary["shared_actor"]["scan_complete"] is False
    assert summary["shared_actor"]["stale_deactivated"] == 0
    links = [
        link
        for link in await env.cross.list_between(left, right)
        if link.relation_type == "shared_actor" and link.is_active
    ]
    assert str(links[0].fingerprint) == stale_fp
    await env.db.dispose()
