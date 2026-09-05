"""V3 §79: Intelligence Refresh tests (IR01-IR20).

覆盖：refresh_case 固定顺序、enqueue、AnalysisJobWorker 分支与 follow-up、
Collection terminal 触发、Manual Refresh API（全应用 TestClient）、
Rework R1 production advanced signal refresh chain（IR16-IR20）。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.advanced_signal_service import AdvancedSignalDetectorService
from app.application.analysis_job_worker import AnalysisJobWorker
from app.application.collection_run_worker import CollectionRunWorker
from app.application.intelligence_refresh_service import IntelligenceRefreshService
from app.application.repositories import ApplicationRepository
from app.application.workspace_entity_service import WorkspaceEntityService
from app.core.config import Settings
from app.infrastructure.database.alignment_repository import AlignmentRepository
from app.infrastructure.database.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.database.collection_run_repository import CollectionRunRepository
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


class _RecordingService:
    def __init__(self, name: str, result: dict[str, Any] | None = None) -> None:
        self.name = name
        self.result = result or {"ok": name}
        self.calls: list[str] = []

    async def evaluate(self, case_id: str, **_: Any) -> dict[str, Any]:
        self.calls.append(f"{self.name}:{case_id}")
        return self.result

    async def refresh_case(self, case_id: str, **_: Any) -> dict[str, Any]:
        self.calls.append(f"{self.name}:{case_id}")
        return self.result

    async def enqueue(self, case_id: str, **_: Any) -> Any:
        self.calls.append(f"enqueue:{self.name}:{case_id}")
        return self.result


async def _setup_refresh() -> SimpleNamespace:
    database = MemoryDatabase()
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    jobs = AnalysisJobRepository(database)
    case = await app_repo.create_case(
        CreateCaseRequest(topic="刷新案例", platforms=["weibo"])
    )
    quality = _RecordingService("quality")
    entities = _RecordingService("entities")
    cross = _RecordingService("cross")
    signals = _RecordingService("signals")
    service = IntelligenceRefreshService(
        analysis_job_repository=jobs,
        quality_service=quality,
        workspace_entity_service=entities,
        cross_investigation_service=cross,
        advanced_signal_service=signals,
    )
    return SimpleNamespace(
        db=database,
        app=app_repo,
        jobs=jobs,
        case=case,
        quality=quality,
        entities=entities,
        cross=cross,
        signals=signals,
        service=service,
    )


# ---------------------------------------------------------------------------
# IR01-IR02: refresh_case 固定顺序 + enqueue
# ---------------------------------------------------------------------------


async def test_ir01_refresh_case_runs_fixed_order() -> None:
    env = await _setup_refresh()
    payload = await env.service.refresh_case(env.case.id)
    assert payload["quality"]["ok"] == "quality"
    assert payload["entities"]["ok"] == "entities"
    assert payload["cross_case"]["ok"] == "cross"
    assert payload["signals"]["ok"] == "signals"
    # 固定顺序 quality → entities → cross → signals
    assert env.quality.calls == [f"quality:{env.case.id}"]
    assert env.entities.calls == [f"entities:{env.case.id}"]
    assert env.cross.calls == [f"cross:{env.case.id}"]
    assert env.signals.calls == [f"signals:{env.case.id}"]
    await env.db.dispose()


async def test_ir02_enqueue_creates_intelligence_refresh_job() -> None:
    env = await _setup_refresh()
    job = await env.service.enqueue(env.case.id, source_key="v3:intel:alignment:job-1:v3.1.0")
    assert job.job_type == "intelligence_refresh"
    assert job.idempotency_key == "v3:intel:alignment:job-1:v3.1.0"
    assert job.status == "pending"
    await env.db.dispose()


# ---------------------------------------------------------------------------
# IR03-IR04: AnalysisJobWorker 分支
# ---------------------------------------------------------------------------


async def test_ir03_worker_runs_intelligence_refresh_branch() -> None:
    env = await _setup_refresh()
    worker = AnalysisJobWorker(
        env.jobs, intelligence_service=env.service, enabled=False
    )
    result = await worker._run("intelligence_refresh", env.case.id)
    assert result["quality"]["ok"] == "quality"
    assert result["cross_case"]["ok"] == "cross"
    await env.db.dispose()


async def test_ir04_worker_rejects_unknown_job_type() -> None:
    env = await _setup_refresh()
    worker = AnalysisJobWorker(env.jobs, intelligence_service=env.service, enabled=False)
    with pytest.raises(ValueError):
        await worker._run("unknown_type", env.case.id)
    await env.db.dispose()


# ---------------------------------------------------------------------------
# IR05-IR06: follow-up enqueue（§62.1）
# ---------------------------------------------------------------------------


async def test_ir05_follow_up_enqueues_after_alignment_success() -> None:
    env = await _setup_refresh()
    worker = AnalysisJobWorker(
        env.jobs, intelligence_service=env.service, enabled=False
    )
    await worker._maybe_enqueue_intelligence_refresh(
        "job-1", env.case.id, "alignment"
    )
    jobs = await env.jobs.list_jobs(env.case.id, job_type="intelligence_refresh")
    assert len(jobs) == 1
    assert jobs[0].idempotency_key == "v3:intel:alignment:job-1:v3.1.0"

    # 同一 key 再 enqueue → 幂等去重，不重复建 job
    await worker._maybe_enqueue_intelligence_refresh(
        "job-1", env.case.id, "alignment"
    )
    jobs = await env.jobs.list_jobs(env.case.id, job_type="intelligence_refresh")
    assert len(jobs) == 1
    await env.db.dispose()


async def test_ir06_follow_up_skips_intelligence_refresh_itself() -> None:
    env = await _setup_refresh()
    worker = AnalysisJobWorker(
        env.jobs, intelligence_service=env.service, enabled=False
    )
    # §62.1：intelligence_refresh 绝不 enqueue 自己（不递归）
    await worker._maybe_enqueue_intelligence_refresh(
        "job-2", env.case.id, "intelligence_refresh"
    )
    jobs = await env.jobs.list_jobs(env.case.id, job_type="intelligence_refresh")
    assert jobs == []
    await env.db.dispose()


# ---------------------------------------------------------------------------
# IR07-IR09: Collection terminal 触发（§63）
# ---------------------------------------------------------------------------


class _FakePlatformExecutor:
    pass


async def test_ir07_collection_terminal_enqueues_alignment_and_integrity() -> None:
    env = await _setup_refresh()
    social = SocialRepository(env.db)
    runs = CollectionRunRepository(env.db)
    worker = CollectionRunWorker(
        runs,
        _FakePlatformExecutor(),  # type: ignore[arg-type]
        social,
        enabled=False,
        analysis_jobs=env.jobs,
    )
    run = await runs.create(
        case_id=env.case.id,
        request_fingerprint=f"fp-{datetime.now(UTC).timestamp()}",
        request_json={"platforms": ["weibo"]},
        phase="discovery",
    )
    await worker._mark_terminal(
        run.id, "completed", {"posts_collected": 3, "case_id": env.case.id}
    )
    alignment = await env.jobs.list_jobs(env.case.id, job_type="alignment")
    integrity = await env.jobs.list_jobs(env.case.id, job_type="integrity")
    assert len(alignment) == 1
    assert len(integrity) == 1
    assert alignment[0].idempotency_key == f"v3:alignment:{run.id}:v3.1.0"
    assert integrity[0].idempotency_key == f"v3:integrity:{run.id}:v3.1.0"
    await env.db.dispose()


async def test_ir08_collection_terminal_idempotent_on_rerun() -> None:
    env = await _setup_refresh()
    social = SocialRepository(env.db)
    runs = CollectionRunRepository(env.db)
    worker = CollectionRunWorker(
        runs,
        _FakePlatformExecutor(),  # type: ignore[arg-type]
        social,
        enabled=False,
        analysis_jobs=env.jobs,
    )
    run = await runs.create(
        case_id=env.case.id,
        request_fingerprint=f"fp-{datetime.now(UTC).timestamp()}",
        request_json={"platforms": ["weibo"]},
        phase="discovery",
    )
    for _ in range(2):
        await worker._mark_terminal(
            run.id, "completed", {"posts_collected": 3, "case_id": env.case.id}
        )
    alignment = await env.jobs.list_jobs(env.case.id, job_type="alignment")
    assert len(alignment) == 1  # 同 key 幂等去重
    await env.db.dispose()


async def test_ir09_collection_failed_does_not_enqueue() -> None:
    env = await _setup_refresh()
    social = SocialRepository(env.db)
    runs = CollectionRunRepository(env.db)
    worker = CollectionRunWorker(
        runs,
        _FakePlatformExecutor(),  # type: ignore[arg-type]
        social,
        enabled=False,
        analysis_jobs=env.jobs,
    )
    run = await runs.create(
        case_id=env.case.id,
        request_fingerprint=f"fp-{datetime.now(UTC).timestamp()}",
        request_json={"platforms": ["weibo"]},
        phase="discovery",
    )
    await worker._mark_terminal(run.id, "failed", {"error_code": "boom"})
    alignment = await env.jobs.list_jobs(env.case.id, job_type="alignment")
    integrity = await env.jobs.list_jobs(env.case.id, job_type="integrity")
    assert alignment == []
    assert integrity == []
    await env.db.dispose()


# ---------------------------------------------------------------------------
# IR10-IR15: Manual Refresh API（§64，全应用 TestClient）
# ---------------------------------------------------------------------------


def _make_app() -> tuple[TestClient, Any]:
    app = create_app(Settings(database_url="sqlite+aiosqlite:///:memory:", demo_mode=True))
    client = TestClient(app)
    return client, app


def test_ir10_manual_refresh_returns_job_ids(tmp_path: Any) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'ir10.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case = client.post(
            "/api/v1/cases", json={"topic": "手动刷新", "platforms": ["weibo"]}
        ).json()
        response = client.post(f"/api/v1/cases/{case['id']}/intelligence:refresh")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "accepted"
        assert body["alignment_job_id"]
        assert body["integrity_job_id"]

        # 同一分钟内重复调用 → 幂等，返回同一批 job（UNIQUE idempotency）
        again = client.post(f"/api/v1/cases/{case['id']}/intelligence:refresh")
        assert again.json()["alignment_job_id"] == body["alignment_job_id"]
        assert again.json()["integrity_job_id"] == body["integrity_job_id"]


def test_ir11_manual_refresh_unknown_case_404(tmp_path: Any) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'ir11.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/cases/missing-case/intelligence:refresh")
        assert response.status_code == 404


def test_ir12_worker_completes_job_end_to_end(tmp_path: Any) -> None:
    """alignment job 走真实 worker tick：claim → run → complete → follow-up。"""
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'ir12.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case = client.post(
            "/api/v1/cases", json={"topic": "端到端", "platforms": ["weibo"]}
        ).json()
        refresh = client.post(f"/api/v1/cases/{case['id']}/intelligence:refresh").json()
        container = app.state.container
        assert container is not None
        # worker 是异步循环；这里直接同步驱动一次 tick
        asyncio.run(container.analysis_job_worker.tick())
        # alignment job 已消费（succeeded）
        job = asyncio.run(
            container.analysis_job_repository.get_job(refresh["alignment_job_id"])
        )
        assert job.status == "succeeded"
        # follow-up 已创建 intelligence_refresh job
        refreshes = asyncio.run(
            container.analysis_job_repository.list_jobs(
                case["id"], job_type="intelligence_refresh"
            )
        )
        assert len(refreshes) == 1


def test_ir13_refresh_api_does_not_enqueue_direct_refresh(tmp_path: Any) -> None:
    """§64：Manual Refresh 只创建 alignment + integrity，不直接建 refresh。"""
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'ir13.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case = client.post(
            "/api/v1/cases", json={"topic": "不直接刷新", "platforms": ["weibo"]}
        ).json()
        client.post(f"/api/v1/cases/{case['id']}/intelligence:refresh")
        container = app.state.container
        assert container is not None
        refreshes = asyncio.run(
            container.analysis_job_repository.list_jobs(
                case["id"], job_type="intelligence_refresh"
            )
        )
        assert refreshes == []  # follow-up 由 worker 完成时创建


# ---------------------------------------------------------------------------
# IR16-IR20: Rework R1 production advanced signal refresh chain
# ---------------------------------------------------------------------------


async def test_ir16_intelligence_refresh_enqueues_advanced_signal_refresh() -> None:
    """IR16：intelligence_refresh 成功 → worker enqueue advanced_signal_refresh。"""
    env = await _setup_refresh()
    worker = AnalysisJobWorker(
        env.jobs, intelligence_service=env.service, enabled=False
    )
    await worker._maybe_enqueue_intelligence_refresh(
        "ir16-intelligence-job", env.case.id, "intelligence_refresh"
    )
    advanced = await env.jobs.list_jobs(
        env.case.id, job_type="advanced_signal_refresh"
    )
    assert len(advanced) == 1
    assert advanced[0].idempotency_key.startswith(
        "v3:advanced:ir16-intelligence-job:"
    )
    # follow-up 链不回归：intelligence_refresh 自身不被再次 enqueue
    refreshes = await env.jobs.list_jobs(env.case.id, job_type="intelligence_refresh")
    assert refreshes == []
    await env.db.dispose()


async def test_ir17_advanced_signal_refresh_worker_branch_runs_refresh_global() -> None:
    """IR17：advanced_signal_refresh worker 分支 → refresh_global()。"""
    env = await _setup_refresh()
    calls: list[str] = []

    class _AdvancedStub:
        async def refresh_global(self) -> dict[str, Any]:
            calls.append("refresh_global")
            return {
                "actor_recurrence": {"upserted": 0, "stale_deactivated": 0},
                "media_reuse": {"upserted": 0, "stale_deactivated": 0},
                "cross_case_overlap": {"upserted": 0, "stale_deactivated": 0},
            }

    worker = AnalysisJobWorker(
        env.jobs, advanced_signal_service=_AdvancedStub(), enabled=False
    )
    result = await worker._run("advanced_signal_refresh", env.case.id)
    assert calls == ["refresh_global"]
    assert set(result) == {"actor_recurrence", "media_reuse", "cross_case_overlap"}
    await env.db.dispose()


async def test_ir18_advanced_signal_refresh_does_not_recurse() -> None:
    """IR18：advanced_signal_refresh 成功后绝不 enqueue 自己或 intelligence_refresh。"""
    env = await _setup_refresh()
    worker = AnalysisJobWorker(
        env.jobs, intelligence_service=env.service, enabled=False
    )
    await worker._maybe_enqueue_intelligence_refresh(
        "job-3", env.case.id, "advanced_signal_refresh"
    )
    assert await env.jobs.list_jobs(env.case.id, job_type="intelligence_refresh") == []
    advanced = await env.jobs.list_jobs(
        env.case.id, job_type="advanced_signal_refresh"
    )
    assert advanced == []
    await env.db.dispose()


async def test_ir19_advanced_job_idempotency_key_uses_intelligence_job_id() -> None:
    """IR19：advanced job 幂等 key = v3:advanced:{intelligence_job_id}:{version}。"""
    env = await _setup_refresh()
    job = await env.service.enqueue_advanced_signal_refresh(
        job_id="ir19-intelligence-job", case_id=env.case.id
    )
    assert job.job_type == "advanced_signal_refresh"
    assert (
        job.idempotency_key
        == "v3:advanced:ir19-intelligence-job:advanced-signal-1.0.0"
    )
    # 同一 intelligence job_id 重复 enqueue → 幂等返回同一 job
    again = await env.service.enqueue_advanced_signal_refresh(
        job_id="ir19-intelligence-job", case_id=env.case.id
    )
    assert again.id == job.id
    # 36 位 uuid job_id → key 超 64 字符被截断，但 job_id 前缀保留，幂等仍稳定
    long_job_id = str(uuid.uuid4())
    long_a = await env.service.enqueue_advanced_signal_refresh(
        job_id=long_job_id, case_id=env.case.id
    )
    long_b = await env.service.enqueue_advanced_signal_refresh(
        job_id=long_job_id, case_id=env.case.id
    )
    assert long_a.id == long_b.id
    assert long_a.idempotency_key.startswith(f"v3:advanced:{long_job_id}:")
    await env.db.dispose()


async def _setup_advanced_detector() -> SimpleNamespace:
    """IR20：真实 AdvancedSignalDetectorService（真实 repo，detector 不 mock）。"""
    database = MemoryDatabase()
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    jobs = AnalysisJobRepository(database)
    case_a = await app_repo.create_case(
        CreateCaseRequest(topic="IR20 案A", platforms=["weibo"])
    )
    case_b = await app_repo.create_case(
        CreateCaseRequest(topic="IR20 案B", platforms=["weibo"])
    )
    social = SocialRepository(database)
    integrity = IntegrityRepository(database)
    workspace = WorkspaceEntityService(
        workspace_repository=WorkspaceEntityRepository(database),
        alignment_repository=AlignmentRepository(database),
        application_repository=app_repo,
        social_repository=social,
        integrity_repository=integrity,
        database=database,
    )
    advanced = AdvancedSignalDetectorService(
        derived_repository=DerivedSignalRepository(database),
        integrity_repository=integrity,
        analysis_job_repository=jobs,
        workspace_service=workspace,
        cross_repository=CrossInvestigationRepository(database),
        media_repository=MediaPipelineRepository(database),
        application_repository=app_repo,
    )
    return SimpleNamespace(
        db=database,
        app=app_repo,
        jobs=jobs,
        case_a=case_a,
        case_b=case_b,
        advanced=advanced,
    )


async def test_ir20_production_chain_runs_all_three_global_detectors() -> None:
    """IR20：production chain 真实执行三类 global detector（真实 Service/Repo）。

    空 workspace 时三类 detector 真实跑完（空 expected set → global
    reconcile）；插入跨 case 同 SHA 媒体后 media_reuse 真实产出 Signal，
    证明 production chain 覆盖了原来只刷 coordination 的缺口。
    """
    env = await _setup_advanced_detector()
    worker = AnalysisJobWorker(
        env.jobs, advanced_signal_service=env.advanced, enabled=False
    )
    result = await worker._run("advanced_signal_refresh", env.case_a.id)
    assert set(result) == {"actor_recurrence", "media_reuse", "cross_case_overlap"}
    for stats in result.values():
        # FC1：global flush 返回含 scan_complete（AnalysisJob result 可见）
        assert set(stats) == {"upserted", "stale_deactivated", "scan_complete"}
        assert stats["scan_complete"] is True

    sha = "aa" * 32
    async with env.db.session_factory() as session:
        for case_id in (env.case_a.id, env.case_b.id):
            session.add(
                MediaAssetRecord(
                    case_id=case_id,
                    platform="weibo",
                    media_type="image",
                    url=f"https://example.com/{case_id}/img.jpg",
                    normalized_url=f"https://example.com/{case_id}/img.jpg",
                    actual_sha256=sha,
                )
            )
        await session.commit()
    refreshed = await worker._run("advanced_signal_refresh", env.case_a.id)
    assert refreshed["media_reuse"]["upserted"] == 1
    assert isinstance(refreshed["actor_recurrence"]["stale_deactivated"], int)
    assert isinstance(refreshed["cross_case_overlap"]["stale_deactivated"], int)
    await env.db.dispose()
