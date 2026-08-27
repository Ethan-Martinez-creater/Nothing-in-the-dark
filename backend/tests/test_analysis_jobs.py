"""Tests for async analysis jobs (A-02)."""

from __future__ import annotations

import asyncio
import atexit
import shutil
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.analysis_job_worker import AnalysisJobWorker
from app.application.integrity_service import IntegrityService
from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.database.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.database.integrity_repository import IntegrityRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.main import create_app
from app.schemas.cases import CreateCaseRequest

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-job-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


async def test_job_worker_executes_integrity() -> None:
    database = Database(f"sqlite+aiosqlite:///{_tmp_db()}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="异步任务", platforms=["weibo"]))
    job_repo = AnalysisJobRepository(database)
    integ_repo = IntegrityRepository(database)
    social = SocialRepository(database)
    integrity_service = IntegrityService(integ_repo, app_repo, social)

    job = await job_repo.create_job(case_id=case.id, job_type="integrity")
    assert job.status == "pending"

    worker = AnalysisJobWorker(job_repo, integrity_service=integrity_service, enabled=False)
    await worker.tick()

    result = await job_repo.get_job(job.id)
    assert result.status == "succeeded"
    assert "assessments" in result.result_json


async def test_job_claim_lease() -> None:
    database = Database(f"sqlite+aiosqlite:///{_tmp_db()}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="租约", platforms=["weibo"]))
    job_repo = AnalysisJobRepository(database)
    await job_repo.create_job(case_id=case.id, job_type="alignment")

    claimed = await job_repo.claim_job("w1", 600)
    assert claimed is not None and claimed.status == "running"
    # 租约未过期，另一个 worker 不应领取同一 job。
    second = await job_repo.claim_job("w2", 600)
    assert second is None or second.id != claimed.id


def test_api_analyze_returns_202() -> None:
    db_path = _tmp_db()
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    asyncio.run(database.create_schema())
    app_repo = ApplicationRepository(database)

    async def seed() -> str:
        case = await app_repo.create_case(CreateCaseRequest(topic="异步 API", platforms=["weibo"]))
        return case.id

    case_id = asyncio.run(seed())
    asyncio.run(database.dispose())

    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        response = client.post(f"/api/v1/cases/{case_id}/alignments:analyze")
        assert response.status_code == 202
        payload = response.json()
        assert "job_id" in payload
        # 查询任务状态。
        status = client.get(f"/api/v1/cases/{case_id}/jobs/{payload['job_id']}")
        assert status.status_code == 200


async def test_job_idempotency_retry_terminal_and_cancel() -> None:
    database = Database(f"sqlite+aiosqlite:///{_tmp_db()}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="任务恢复", platforms=["weibo"]))
    repo = AnalysisJobRepository(database)
    first = await repo.create_job(
        case_id=case.id,
        job_type="alignment",
        idempotency_key="same-request",
        max_attempts=1,
    )
    repeated = await repo.create_job(
        case_id=case.id,
        job_type="alignment",
        idempotency_key="same-request",
        max_attempts=1,
    )
    assert first.id == repeated.id

    claimed = await repo.claim_job("worker-a", 30)
    assert claimed is not None and claimed.attempt == 1
    assert await repo.fail_job(claimed.id, "worker-a", "expected_failure")
    terminal = await repo.get_job(claimed.id)
    assert terminal.status == "failed_terminal"
    assert await repo.claim_job("worker-b", 30) is None

    pending = await repo.create_job(case_id=case.id, job_type="integrity")
    cancelled = await repo.request_cancel(pending.id)
    assert cancelled.status == "cancelled"
    assert await repo.claim_job("worker-b", 30) is None


async def test_job_completion_rejected_after_lease_owner_changes() -> None:
    database = Database(f"sqlite+aiosqlite:///{_tmp_db()}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="租约隔离", platforms=["weibo"]))
    repo = AnalysisJobRepository(database)
    job = await repo.create_job(case_id=case.id, job_type="alignment")
    claimed = await repo.claim_job("worker-a", 30)
    assert claimed is not None
    await repo.update_job(job.id, lease_owner="worker-b")
    assert not await repo.complete_job(job.id, "worker-a", {"bad": True})
    current = await repo.get_job(job.id)
    assert current.status == "running"
    assert current.result_json == {}


async def test_expired_final_attempt_is_terminalized() -> None:
    database = Database(f"sqlite+aiosqlite:///{_tmp_db()}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="最终尝试崩溃", platforms=["weibo"]))
    repo = AnalysisJobRepository(database)
    job = await repo.create_job(case_id=case.id, job_type="alignment", max_attempts=1)
    claimed = await repo.claim_job("dead-worker", 0)
    assert claimed is not None and claimed.attempt == 1
    assert await repo.claim_job("recovery-worker", 30) is None
    current = await repo.get_job(job.id)
    assert current.status == "failed_terminal"
    assert current.error_code == "lease_expired_after_max_attempts"
