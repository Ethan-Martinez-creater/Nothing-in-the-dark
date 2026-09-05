"""V3 Final Closure FC2/FC-E2E: Case / Project 删除后的 global refresh。

FC2-T01~T06：删除 Case / Project 后 enqueue 一次 advanced_signal_refresh
（剩余 Case 作 FK anchor；最后一个 Case 删除不 enqueue；enqueue 失败不影响
删除）。FC-E2E-01~03：真实 Service/Repository/Worker 的分页完整性 fail-safe
与 delete → global refresh 生产链路。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.application.advanced_signal_service import AdvancedSignalDetectorService
from app.application.analysis_job_worker import AnalysisJobWorker
from app.application.cross_investigation_service import CrossInvestigationService
from app.application.intelligence_refresh_service import IntelligenceRefreshService
from app.application.repositories import ApplicationRepository
from app.application.workspace_entity_service import WorkspaceEntityService
from app.core.config import Settings
from app.infrastructure.database.alignment_repository import AlignmentRepository
from app.infrastructure.database.analysis_job_repository import AnalysisJobRepository
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
from app.infrastructure.database.models import MediaAssetRecord
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.database.workspace_entity_repository import (
    WorkspaceEntityRepository,
)
from app.main import create_app
from app.schemas.cases import CreateCaseRequest
from tests.memory_db import MemoryDatabase


def _post(platform: str, native_id: str, author_id: str) -> dict[str, Any]:
    return {
        "platform": platform,
        "native_id": native_id,
        "content_type": "post",
        "title": "",
        "content": "FC 删除场景讨论",
        "author": f"author-{author_id}",
        "published_at": "2026-08-15T10:00:00+08:00",
        "engagement": 5,
        "metrics": {"total": 5},
        "url": f"https://example.com/{platform}/{native_id}",
        "raw": {"id": native_id, "user_id": author_id},
        "comments": [],
    }


async def _setup(*, case_count: int = 3) -> SimpleNamespace:
    database = MemoryDatabase()
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    workspace_repo = WorkspaceEntityRepository(database)
    social_repo = SocialRepository(database)
    media_repo = MediaPipelineRepository(database)
    integrity_repo = IntegrityRepository(database)
    jobs_repo = AnalysisJobRepository(database)
    workspace_service = WorkspaceEntityService(
        workspace_repository=workspace_repo,
        alignment_repository=AlignmentRepository(database),
        application_repository=app_repo,
        social_repository=social_repo,
        integrity_repository=integrity_repo,
        database=database,
    )
    cross_repo = CrossInvestigationRepository(database)
    cross_service = CrossInvestigationService(
        cross_repository=cross_repo,
        workspace_repository=workspace_repo,
        workspace_service=workspace_service,
        social_repository=social_repo,
        media_repository=media_repo,
        application_repository=app_repo,
        database=database,
    )
    derived_repo = DerivedSignalRepository(database)
    advanced = AdvancedSignalDetectorService(
        derived_repository=derived_repo,
        integrity_repository=integrity_repo,
        analysis_job_repository=jobs_repo,
        workspace_service=workspace_service,
        cross_repository=cross_repo,
        media_repository=media_repo,
        application_repository=app_repo,
    )
    refresh = IntelligenceRefreshService(
        analysis_job_repository=jobs_repo,
        application_repository=app_repo,
        quality_service=SimpleNamespace(evaluate=_async_noop()),
        workspace_entity_service=workspace_service,
        cross_investigation_service=cross_service,
        advanced_signal_service=advanced,
    )
    worker = AnalysisJobWorker(
        jobs_repo,
        advanced_signal_service=advanced,
        enabled=False,
    )
    cases = []
    for index in range(case_count):
        cases.append(
            await app_repo.create_case(
                CreateCaseRequest(topic=f"FC 删除案{index}", platforms=["weibo"])
            )
        )
    return SimpleNamespace(
        db=database,
        app=app_repo,
        social=social_repo,
        media=media_repo,
        cross=cross_repo,
        cross_service=cross_service,
        derived=derived_repo,
        jobs=jobs_repo,
        workspace_service=workspace_service,
        advanced=advanced,
        refresh=refresh,
        worker=worker,
        cases=cases,
    )


def _async_noop() -> Any:
    async def _inner(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    return _inner


async def _seed_shared_actor_across(env: SimpleNamespace) -> None:
    """A 账号 + B/C 帖子作者 → 同一 identity component 出现在 3 个 Case。"""
    anchor = env.cases[0]
    await env.app.upsert_account(
        case_id=anchor.id,
        platform="weibo",
        native_id="fc-rec",
        name="复现主体",
        normalized_name="复现主体",
    )
    for case in env.cases[1:]:
        await env.social.persist_batch(
            case_id=case.id,
            posts=[_post("weibo", f"p-{case.id[:6]}", "fc-rec")],
        )
    for case in env.cases:
        await env.workspace_service.refresh_case(case.id)


def _actor_signal_fp(component_key: str) -> str:
    import hashlib

    return hashlib.sha256(
        ("actor_recurrence" + component_key + "advanced-signal-1.0.0").encode()
    ).hexdigest()


async def _active_actor_signal(env: SimpleNamespace) -> Any:
    signals = [
        s
        for s in await env.derived.list()
        if s.signal_type == "actor_recurrence" and s.detector_active
    ]
    assert len(signals) == 1
    return signals[0]


# ---------------------------------------------------------------------------
# FC2-T01~T03: delete → follow-up refresh → signal resolved
# ---------------------------------------------------------------------------


async def test_fc2_t01_actor_signal_resolved_after_case_delete() -> None:
    env = await _setup()
    await _seed_shared_actor_across(env)
    await env.advanced.refresh_actor_recurrence()
    signal = await _active_actor_signal(env)
    assert signal.status == "open"

    deleted = env.cases[2]
    await env.app.delete_case(deleted.id)
    job = await env.refresh.enqueue_after_scope_deletion(scope_key=deleted.id)
    assert job is not None
    assert job.job_type == "advanced_signal_refresh"
    assert job.idempotency_key.startswith(f"v3:advanced:case-delete:{deleted.id}:")
    # anchor = remaining cases 首个（created_at ASC + id ASC）→ 不能是已删除 Case
    remaining = await env.app.list_cases_ordered_by_creation()
    assert str(job.case_id) == remaining[0].id

    result = await env.worker._run("advanced_signal_refresh", str(job.case_id))
    records = [
        s
        for s in await env.derived.list()
        if s.signal_type == "actor_recurrence"
    ]
    # primary 恰为被删 Case 时由 V3 cleanup 直接删除；否则 global
    # reconcile 置 inactive+resolved——两者都不保留错误 active。
    if records:
        assert result["actor_recurrence"]["stale_deactivated"] == 1
        assert records[0].detector_active is False
        assert records[0].status == "resolved"
    else:
        assert result["actor_recurrence"]["stale_deactivated"] == 0
    await env.db.dispose()


async def test_fc2_t02_media_signal_resolved_after_case_delete() -> None:
    env = await _setup(case_count=2)
    case_a, case_b = env.cases
    sha = "ab" * 32
    for case_id in (case_a.id, case_b.id):
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
    await env.advanced.refresh_media_reuse()
    signals = [
        s
        for s in await env.derived.list()
        if s.signal_type == "media_reuse" and s.detector_active
    ]
    assert len(signals) == 1

    await env.app.delete_case(case_b.id)
    job = await env.refresh.enqueue_after_scope_deletion(scope_key=case_b.id)
    assert job is not None
    await env.worker._run("advanced_signal_refresh", str(job.case_id))
    records = [
        s
        for s in await env.derived.list()
        if s.signal_type == "media_reuse"
    ]
    # primary 恰为被删 Case 时由 cleanup 删除；否则 reconcile resolved。
    assert records == [] or (
        records[0].detector_active is False
        and records[0].status == "resolved"
    )
    await env.db.dispose()


async def test_fc2_t03_overlap_not_active_after_case_delete() -> None:
    env = await _setup(case_count=2)
    case_a, case_b = env.cases
    for index in range(3):
        await env.app.upsert_account(
            case_id=case_a.id,
            platform="weibo",
            native_id=f"ov{index}",
            name=f"重叠主体{index}",
            normalized_name=f"重叠主体{index}",
        )
        await env.social.persist_batch(
            case_id=case_b.id,
            posts=[_post("weibo", f"ov-p{index}", f"ov{index}")],
        )
    for sha_index in range(2):
        sha = f"{sha_index:02x}" * 32
        for case_id in (case_a.id, case_b.id):
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
    await env.workspace_service.refresh_case(case_a.id)
    await env.workspace_service.refresh_case(case_b.id)
    await env.cross_service.refresh_case(case_a.id)
    await env.advanced.refresh_cross_case_overlap()
    overlaps = [
        s
        for s in await env.derived.list()
        if s.signal_type == "cross_case_overlap" and s.detector_active
    ]
    assert len(overlaps) == 1  # actor 1.0*0.40 + media 1.0*0.30 = 0.70

    await env.app.delete_case(case_b.id)
    job = await env.refresh.enqueue_after_scope_deletion(scope_key=case_b.id)
    assert job is not None
    await env.worker._run("advanced_signal_refresh", str(job.case_id))
    overlaps = [
        s
        for s in await env.derived.list()
        if s.signal_type == "cross_case_overlap"
    ]
    # 最终不允许保留错误 active 状态（resolved 或 cleanup 删除均可）
    assert all(s.detector_active is False for s in overlaps)
    await env.db.dispose()


# ---------------------------------------------------------------------------
# FC2-T04~T06: API 层删除（最后 Case / enqueue 失败 / Project 一次性）
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Any, name: str):
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / name}.db",
            demo_mode=True,
        )
    )
    return TestClient(app), app


def test_fc2_t04_delete_last_case_no_job(tmp_path: Any) -> None:
    client, app = _make_app(tmp_path, "fc2t04")
    with client:
        case = client.post(
            "/api/v1/cases", json={"topic": "唯一调查", "platforms": ["weibo"]}
        ).json()
        response = client.delete(f"/api/v1/cases/{case['id']}")
        assert response.status_code == 204
        container = app.state.container
        jobs = asyncio.run(
            container.analysis_job_repository.list_jobs(
                case["id"], job_type="advanced_signal_refresh"
            )
        )
        assert jobs == []  # 最后一个 Case 删除 → 不 enqueue、无非法 FK
        cases = asyncio.run(container.repository.list_cases())
        assert cases == []


def test_fc2_t05_enqueue_failure_does_not_fail_delete(tmp_path: Any) -> None:
    client, app = _make_app(tmp_path, "fc2t05")
    with client:
        case_a = client.post(
            "/api/v1/cases", json={"topic": "调查A", "platforms": ["weibo"]}
        ).json()
        client.post(
            "/api/v1/cases", json={"topic": "调查B", "platforms": ["weibo"]}
        )
        container = app.state.container

        async def _broken(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("enqueue down")

        import asyncio as _asyncio

        container.intelligence_refresh_service.enqueue_after_scope_deletion = _broken
        response = client.delete(f"/api/v1/cases/{case_a['id']}")
        assert response.status_code == 204  # 删除不受 follow-up 失败影响
        remaining = _asyncio.run(container.repository.list_cases())
        assert [c.id for c in remaining] != [case_a["id"]]


def test_fc2_t06_project_delete_enqueues_once(tmp_path: Any) -> None:
    client, app = _make_app(tmp_path, "fc2t06")
    with client:
        # workspace 里留一个 Project 外的 Case 作为 anchor（Project 全删后
        # remaining 非 0 才会 enqueue；anchor 不能是被删除的 Case）
        outside = client.post(
            "/api/v1/cases", json={"topic": "外部调查", "platforms": ["weibo"]}
        ).json()
        project = client.post(
            "/api/v1/projects", json={"title": "项目组"}
        ).json()
        case_ids = []
        for index in range(3):
            case = client.post(
                "/api/v1/cases",
                json={
                    "topic": f"项目调查{index}",
                    "platforms": ["weibo"],
                    "project_id": project["id"],
                },
            ).json()
            case_ids.append(case["id"])
        response = client.delete(f"/api/v1/projects/{project['id']}")
        assert response.status_code == 204
        container = app.state.container
        jobs_on_outside = asyncio.run(
            container.analysis_job_repository.list_jobs(
                outside["id"], job_type="advanced_signal_refresh"
            )
        )
        # 3 个 Case 的 Project 删除 → 只 enqueue 一次 global refresh（不按
        # Case 数重复），anchor = remaining cases 首个（= Project 外 Case）
        assert len(jobs_on_outside) == 1
        assert jobs_on_outside[0].idempotency_key.startswith(
            f"v3:advanced:case-delete:{project['id']}:"
        )
        assert str(jobs_on_outside[0].case_id) == outside["id"]
        remaining = asyncio.run(container.repository.list_cases())
        assert [c.id for c in remaining] == [outside["id"]]


# ---------------------------------------------------------------------------
# FC-E2E-01~03: 真实链路 fail-safe / delete → worker
# ---------------------------------------------------------------------------


async def test_fc_e2e_01_large_domain_overlap_multi_page(monkeypatch) -> None:
    """真实 CrossRepository + page size 3：>3 links 跨至少两页，完整
    expected、正确 Signal、global reconciliation 正确执行。"""
    from app.application import advanced_signal_service as adv_module

    monkeypatch.setattr(adv_module, "_CROSS_LINK_PAGE_SIZE", 3)
    env = await _setup(case_count=2)
    case_a, case_b = env.cases
    for index in range(3):
        await env.app.upsert_account(
            case_id=case_a.id,
            platform="weibo",
            native_id=f"e2e{index}",
            name=f"E2E主体{index}",
            normalized_name=f"E2E主体{index}",
        )
        await env.social.persist_batch(
            case_id=case_b.id,
            posts=[_post("weibo", f"e2e-p{index}", f"e2e{index}")],
        )
    for sha_index in range(2):
        sha = f"{sha_index:02x}" * 32
        for case_id in (case_a.id, case_b.id):
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
    await env.workspace_service.refresh_case(case_a.id)
    await env.workspace_service.refresh_case(case_b.id)
    # 3 shared_actor + 1 shared_media = 4 links > page size 3 → 至少两页
    summary = await env.cross_service.refresh_case(case_a.id)
    assert summary["shared_actor"]["scan_complete"] is True

    # 预置一个已失效（expected 外）的旧 overlap Signal → reconcile 应 resolve
    import hashlib

    stale_fp = hashlib.sha256(
        ("cross_case_overlap" + "case-gone" + "case-other" + "advanced-signal-1.0.0").encode()
    ).hexdigest()
    await env.derived.upsert_observed_signal(
        case_id="case-gone",
        source_type="derived",
        source_id="case-gone:case-other",
        signal_type="cross_case_overlap",
        severity="warning",
        title="旧重叠",
        why_it_matters="旧重叠",
        confidence=None,
        metric_snapshot={},
        evidence_refs=[],
        related_case_ids=["case-gone", "case-other"],
        fingerprint=stale_fp,
        detector_version="advanced-signal-1.0.0",
    )
    result = await env.advanced.refresh_cross_case_overlap()
    assert result["scan_complete"] is True
    records = await env.derived.list()
    stale = [r for r in records if r.fingerprint == stale_fp]
    assert stale[0].detector_active is False  # global reconcile 生效
    active_overlap = [
        r
        for r in records
        if r.signal_type == "cross_case_overlap" and r.detector_active
    ]
    assert len(active_overlap) == 1
    assert active_overlap[0].metric_snapshot_json["overlap_score"] == 0.70
    await env.db.dispose()


async def test_fc_e2e_02_incomplete_scan_failsafe_real_repo(monkeypatch) -> None:
    """FC-E2E-02（最重要 fail-safe）：真实 Repository + cap 触发 → 既有
    active Signal 保持 active。"""
    from app.application import advanced_signal_service as adv_module

    env = await _setup(case_count=2)
    case_a, case_b = env.cases
    for index in range(3):
        await env.app.upsert_account(
            case_id=case_a.id,
            platform="weibo",
            native_id=f"fs{index}",
            name=f"FailSafe主体{index}",
            normalized_name=f"FailSafe主体{index}",
        )
        await env.social.persist_batch(
            case_id=case_b.id,
            posts=[_post("weibo", f"fs-p{index}", f"fs{index}")],
        )
    for sha_index in range(2):
        sha = f"{sha_index:02x}" * 32
        for case_id in (case_a.id, case_b.id):
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
    await env.workspace_service.refresh_case(case_a.id)
    await env.workspace_service.refresh_case(case_b.id)
    await env.cross_service.refresh_case(case_a.id)
    # 首轮：默认 cap 下 2 条 links 完整扫描 → overlap Signal 产出
    result = await env.advanced.refresh_cross_case_overlap()
    assert result["scan_complete"] is True
    active = [
        s
        for s in await env.derived.list()
        if s.signal_type == "cross_case_overlap" and s.detector_active
    ]
    assert len(active) == 1

    # cap=1 < 2 条 links → incomplete → Signal 必须保持 active
    monkeypatch.setattr(adv_module, "MAX_CROSS_LINK_SCAN", 1)
    result = await env.advanced.refresh_cross_case_overlap()
    assert result["scan_complete"] is False
    assert result["stale_deactivated"] == 0
    still_active = [
        s
        for s in await env.derived.list()
        if s.signal_type == "cross_case_overlap" and s.detector_active
    ]
    assert len(still_active) == 1
    await env.db.dispose()


def test_fc_e2e_03_delete_then_global_refresh_production_chain(tmp_path: Any) -> None:
    """FC-E2E-03：真实 Repository delete → API DELETE → follow-up
    advanced_signal_refresh → 真实 worker 消费 → Signal resolved。"""
    client, app = _make_app(tmp_path, "fce2e03")
    with client:
        container = app.state.container
        assert container is not None
        case_ids = []
        for index in range(3):
            case = client.post(
                "/api/v1/cases",
                json={"topic": f"生产链案{index}", "platforms": ["weibo"]},
            ).json()
            case_ids.append(case["id"])
        app_repo = container.repository
        social = container.social

        async def _seed() -> None:
            await app_repo.upsert_account(
                case_id=case_ids[0],
                platform="weibo",
                native_id="prod-rec",
                name="生产链主体",
                normalized_name="生产链主体",
            )
            for case_id in case_ids[1:]:
                await social.persist_batch(
                    case_id=case_id,
                    posts=[_post("weibo", f"prod-p-{case_id[:6]}", "prod-rec")],
                )
            for case_id in case_ids:
                await container.workspace_entities.refresh_case(case_id)
            await container.advanced_signals.refresh_actor_recurrence()

        asyncio.run(_seed())
        derived = container.derived_signal_repository
        active = asyncio.run(
            _pick_active_actor(derived)
        )
        assert active is not None and active.status == "open"

        # 真实 API 删除第三个 Case → follow-up enqueue
        response = client.delete(f"/api/v1/cases/{case_ids[2]}")
        assert response.status_code == 204
        jobs = asyncio.run(
            container.analysis_job_repository.list_jobs(
                case_ids[0], job_type="advanced_signal_refresh"
            )
        )
        assert len(jobs) == 1
        # 真实 worker 消费
        asyncio.run(container.analysis_job_worker.tick())
        job = asyncio.run(
            container.analysis_job_repository.get_job(jobs[0].id)
        )
        assert job.status == "succeeded"
        records = asyncio.run(_list_actor_signals(derived))
        assert len(records) == 1
        assert records[0].detector_active is False
        assert records[0].status == "resolved"


async def _pick_active_actor(derived: Any) -> Any:
    signals = [
        s
        for s in await derived.list()
        if s.signal_type == "actor_recurrence" and s.detector_active
    ]
    return signals[0] if signals else None


async def _list_actor_signals(derived: Any) -> list[Any]:
    return [
        s for s in await derived.list() if s.signal_type == "actor_recurrence"
    ]
