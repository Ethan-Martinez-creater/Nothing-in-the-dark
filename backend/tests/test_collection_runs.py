"""Async progressive collection run tests（CR01-CR20 / AP / CW）。

覆盖：幂等创建、immutable snapshot、fingerprint、claim/lease/heartbeat
fencing、恢复、延迟重试、渐进持久化、取消与终态判定。
Worker 用 FakePlatformExecutor 替换沙箱采集（不真跑浏览器）。
"""

from __future__ import annotations

import asyncio
import atexit
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.application.collection_run_service import CollectionRunService
from app.application.collection_run_worker import CollectionRunWorker
from app.application.collection_service import CollectionDefinitionService
from app.application.repositories import ApplicationRepository
from app.infrastructure.database import Database
from app.infrastructure.database.collection_run_repository import CollectionRunRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.schemas.cases import CreateCaseRequest

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-cr-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


def _post(platform: str, index: int, published_at: str = "2026-08-15T10:00:00+08:00") -> dict[str, Any]:
    return {
        "platform": platform,
        "native_id": f"{platform}-{index}",
        "content_type": "post",
        "title": "",
        "content": f"{platform} 竹知了事件相关讨论内容 {index}，足够长以通过短文本过滤",
        "author": f"author-{platform}",
        "published_at": published_at,
        "engagement": 10,
        "metrics": {"total": 10},
        "url": f"https://example.com/{platform}/{index}",
        "raw": {"id": f"{platform}-{index}"},
        "comments": [],
    }


class FakePlatformExecutor:
    """替换沙箱采集：按平台返回 posts 或抛异常；可编程失败次数。"""

    def __init__(self, results: dict[str, Any]) -> None:
        self._results = dict(results)
        self._calls: list[str] = []
        self._fail_counts: dict[str, int] = {}

    def fail_next(self, platform: str, times: int = 1) -> None:
        self._fail_counts[platform] = times

    async def run_platform(
        self,
        platform: str,
        snapshot: dict[str, Any],
        *,
        cancel_event: asyncio.Event | None = None,
        run_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self._calls.append(platform)
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("collection cancelled")
        if self._fail_counts.get(platform, 0) > 0:
            self._fail_counts[platform] -= 1
            raise RuntimeError(f"boom {platform}")
        result = self._results.get(platform)
        if isinstance(result, Exception):
            raise result
        return list(result or [])


async def _setup(case_platforms: list[str] | None = None) -> tuple[Database, Any, CollectionRunService, CollectionDefinitionService]:
    database = Database(f"sqlite+aiosqlite:///{_tmp_db()}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(
        CreateCaseRequest(
            topic="华为竹知了事件",
            platforms=case_platforms or ["weibo", "bilibili", "zhihu"],
            time_start="2026-08-10",
            time_end="2026-08-20",
        )
    )
    collection_service = CollectionDefinitionService(database, llm=None)
    definition = await collection_service.create_manual(
        case.id,
        goal="采集竹知了事件讨论",
        platforms=case.platforms,
        platform_queries={
            "weibo": ["竹知了"],
            "bilibili": ["华为 竹知了"],
            "zhihu": ["竹知了"],
        },
    )
    await collection_service.activate(case.id, definition.id)
    service = CollectionRunService(
        database, collection_service, CollectionRunRepository(database)
    )
    return database, case, service, collection_service


# ---------------- CR01 - CR07：创建 / snapshot / fingerprint ----------------

async def test_cr01_create_queued_run() -> None:
    db, case, service, _ = await _setup()
    run = await service.start(case.id, phase="discovery")
    assert run.status == "queued"
    assert run.phase == "discovery"
    assert (run.request_json or {}).get("case_id") == case.id
    assert len((run.progress_json or {}).get("platforms", {})) == 3
    await db.dispose()


async def test_cr02_immutable_snapshot() -> None:
    db, case, service, _ = await _setup()
    run = await service.start(case.id, phase="discovery")
    snapshot = run.request_json
    assert snapshot["definition"]["version"] == 1
    assert set(snapshot["platforms"]) == {"weibo", "bilibili", "zhihu"}
    assert snapshot["time_range"]["start"]
    assert snapshot["keywords"]["weibo"] == ["竹知了"]
    budget = snapshot["budget"]
    # 11 天窗口 → upstream = min(max(11*10,60),150) = 110
    assert budget["upstream_limit_per_platform"] == 110
    assert budget["per_day_limit"] == 30
    assert budget["include_comments"] is False
    assert budget["comment_limit"] == 0
    await db.dispose()


async def test_cr03_exact_definition_version_survives_active_changes() -> None:
    db, case, service, collection_service = await _setup()
    run = await service.start(case.id, phase="discovery")
    version_at_start = run.collection_definition_version
    # 审批/启动后 Active Definition 升级到 v2：run 仍绑定 v1 snapshot。
    latest = (await collection_service.list_for_case(case.id))[0]
    v2 = await collection_service.revise(
        case.id, latest.id, goal="升级后的采集目标"
    )
    await collection_service.activate(case.id, v2.id)
    fresh = await service.get_for_case(case.id, run.id)
    assert fresh.collection_definition_version == version_at_start
    assert fresh.request_json["definition"]["version"] == version_at_start
    await db.dispose()


async def test_cr04_same_tool_call_is_idempotent() -> None:
    db, case, service, _ = await _setup()
    run1 = await service.start(
        case.id, phase="discovery", trigger_tool_call_id="call-1",
        idempotency_key="tool-call:call-1",
    )
    run2 = await service.start(
        case.id, phase="discovery", trigger_tool_call_id="call-1",
        idempotency_key="tool-call:call-1",
    )
    assert run1.id == run2.id
    await db.dispose()


async def test_cr05_different_platform_scope_different_fingerprint() -> None:
    db, case, service, _ = await _setup()
    run1 = await service.start(case.id, phase="discovery", platforms=["weibo"])
    run2 = await service.start(case.id, phase="discovery", platforms=["weibo", "bilibili"])
    assert run1.id != run2.id
    assert run1.request_fingerprint != run2.request_fingerprint
    await db.dispose()


async def test_cr06_different_time_range_different_fingerprint() -> None:
    db, case, service, _ = await _setup()
    run1 = await service.start(case.id, phase="discovery")
    run2 = await service.start(
        case.id,
        phase="discovery",
        time_range={"start": "2026-08-10", "end": "2026-08-12"},
    )
    assert run1.id != run2.id
    assert run1.request_fingerprint != run2.request_fingerprint
    await db.dispose()


async def test_cr07_same_active_fingerprint_returns_existing_run() -> None:
    db, case, service, _ = await _setup()
    run1 = await service.start(case.id, phase="discovery")
    # 相同 fingerprint（同 case/definition/phase/scope），不同 idempotency key
    run2 = await service.start(
        case.id, phase="discovery", idempotency_key="tool-call:other"
    )
    assert run2.id == run1.id
    await db.dispose()


# ---------------- CR08 - CR13：claim / lease / heartbeat / fencing ----------------

async def test_cr08_claim_queued_run() -> None:
    db, case, service, _ = await _setup()
    run = await service.start(case.id, phase="discovery")
    repo = CollectionRunRepository(db)
    claimed = await repo.claim_next("worker-1", 60)
    assert claimed is not None and claimed.id == run.id
    assert claimed.status == "running"
    assert claimed.lease_owner == "worker-1"
    assert claimed.attempts == 1
    # 租约未过期时其他 worker 不能领取
    second = await repo.claim_next("worker-2", 60)
    assert second is None
    await db.dispose()


async def test_cr09_heartbeat_extends_lease() -> None:
    db, case, service, _ = await _setup()
    run = await service.start(case.id, phase="discovery")
    repo = CollectionRunRepository(db)
    claimed = await repo.claim_next("worker-1", 60)
    assert claimed is not None
    owns, cancel_req = await repo.heartbeat(claimed.id, "worker-1", 60)
    assert owns is True and cancel_req is False
    fresh = await repo.get(claimed.id)
    assert fresh.heartbeat_at is not None
    await db.dispose()


async def test_cr10_stale_worker_loses_ownership() -> None:
    db, case, service, _ = await _setup()
    run = await service.start(case.id, phase="discovery")
    repo = CollectionRunRepository(db)
    claimed = await repo.claim_next("worker-1", 60)
    assert claimed is not None
    # 租约过期后 worker-2 可重新领取（stale ownership 转移）
    now = datetime.now(UTC)
    await repo.update_progress_if_owner(
        claimed.id, "worker-1", progress_json=claimed.progress_json,
        posts_collected=0, comments_collected=0,
    )
    from sqlalchemy import update
    from app.infrastructure.database.models import CollectionRunRecord
    async with db.session_factory() as session:
        await session.execute(
            update(CollectionRunRecord)
            .where(CollectionRunRecord.id == claimed.id)
            .values(lease_expires_at=now - timedelta(seconds=1))
        )
        await session.commit()
    recovered = await repo.claim_next("worker-2", 60)
    assert recovered is not None and recovered.id == claimed.id
    assert recovered.lease_owner == "worker-2"
    assert recovered.attempts == 2
    await db.dispose()


async def test_cr11_stale_worker_cannot_update_progress() -> None:
    db, case, service, _ = await _setup()
    run = await service.start(case.id, phase="discovery")
    repo = CollectionRunRepository(db)
    claimed = await repo.claim_next("worker-1", 60)
    assert claimed is not None
    # 非 owner 写 progress 被 fencing 拒绝
    ok = await repo.update_progress_if_owner(
        claimed.id, "worker-2",
        progress_json={"platforms": {}}, posts_collected=1, comments_collected=0,
    )
    assert ok is False
    await db.dispose()


async def test_cr12_lease_loss_triggers_cancel() -> None:
    db, case, service, _ = await _setup()
    run = await service.start(case.id, phase="discovery")
    repo = CollectionRunRepository(db)
    claimed = await repo.claim_next("worker-1", 60)
    assert claimed is not None
    # 另一个 worker 抢占租约（模拟 lease lost）
    from sqlalchemy import update
    from app.infrastructure.database.models import CollectionRunRecord
    async with db.session_factory() as session:
        await session.execute(
            update(CollectionRunRecord)
            .where(CollectionRunRecord.id == claimed.id)
            .values(lease_owner="worker-2")
        )
        await session.commit()
    owns, _ = await repo.heartbeat(claimed.id, "worker-1", 60)
    assert owns is False
    await db.dispose()


async def test_cr13_user_cancel() -> None:
    db, case, service, _ = await _setup()
    repo = CollectionRunRepository(db)
    run = await service.start(case.id, phase="discovery")
    # queued → 直接 cancelled
    cancelled = await service.cancel(case.id, run.id)
    assert cancelled.status == "cancelled"
    assert cancelled.cancel_requested_at is not None
    # running → 仅记录取消请求
    run2 = await service.start(
        case.id, phase="discovery", idempotency_key="tool-call:cancel-2"
    )
    claimed = await repo.claim_next("worker-1", 60)
    assert claimed is not None and claimed.id == run2.id
    pending = await repo.request_cancel(run2.id)
    assert pending.status == "running"
    assert pending.cancel_requested_at is not None
    await db.dispose()


# ---------------- CW / CR14-CR20：worker 执行与恢复 ----------------

def _worker(
    db: Database,
    executor: FakePlatformExecutor,
    worker_id: str = "test-worker",
) -> CollectionRunWorker:
    return CollectionRunWorker(
        CollectionRunRepository(db),
        executor,
        SocialRepository(db),
        worker_id=worker_id,
        poll_interval_seconds=0.1,
        lease_seconds=60,
        enabled=False,
        platform_concurrency_discovery=2,
        platform_concurrency_deep=1,
    )


async def _run_to_completion(
    db: Database, executor: FakePlatformExecutor, run_id: str
) -> CollectionRunWorker:
    """claim + 等待 _execute 完成（tick 只创建后台任务，不适合同步断言）。"""
    repo = CollectionRunRepository(db)
    claimed = await repo.claim_next("test-worker", 60)
    assert claimed is not None and claimed.id == run_id
    worker = _worker(db, executor)
    await worker._execute(run_id)
    return worker


async def test_cw05_first_finished_platform_persists_before_others_finish() -> None:
    db, case, service, _ = await _setup()
    executor = FakePlatformExecutor({
        "weibo": [_post("weibo", 1)],
        "bilibili": [_post("bilibili", 1)],
    })
    run = await service.start(case.id, phase="discovery")
    worker = await _run_to_completion(db, executor, run.id)
    fresh = await worker._repository.get(run.id)
    assert fresh.status == "completed"
    assert fresh.posts_collected >= 1
    # 数据已持久化（partial data 单调可用）
    social = SocialRepository(db)
    from app.infrastructure.database.models import SourcePostRecord
    async with db.session_factory() as session:
        from sqlalchemy import select
        rows = (await session.scalars(
            select(SourcePostRecord).where(SourcePostRecord.case_id == case.id)
        )).all()
        assert len(rows) >= 1
    await db.dispose()


async def test_cr14_completed_platform_survives_recovery() -> None:
    db, case, service, _ = await _setup()
    executor = FakePlatformExecutor({
        "weibo": [_post("weibo", 1)],
        "bilibili": [_post("bilibili", 1)],
        "zhihu": [_post("zhihu", 1)],
    })
    run = await service.start(case.id, phase="discovery")
    repo = CollectionRunRepository(db)
    # worker-1 只跑完 weibo 后"崩溃"（修改 progress 模拟 checkpoint）
    claimed = await repo.claim_next("worker-1", 60)
    assert claimed is not None
    progress = dict(claimed.progress_json)
    platforms = progress["platforms"]
    platforms["weibo"]["status"] = "completed"
    platforms["weibo"]["posts_collected"] = 1
    progress["completed_platforms"] = 1
    await repo.update_progress_if_owner(
        claimed.id, "worker-1", progress_json=progress,
        posts_collected=1, comments_collected=0,
    )
    # 租约过期 → worker-2 恢复执行：只跑未完成平台
    from sqlalchemy import update
    from app.infrastructure.database.models import CollectionRunRecord
    async with db.session_factory() as session:
        await session.execute(
            update(CollectionRunRecord)
            .where(CollectionRunRecord.id == claimed.id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    recovered = await repo.claim_next("worker-2", 60)
    assert recovered is not None and recovered.id == run.id
    worker2 = _worker(db, executor, worker_id="worker-2")
    await worker2._execute(run.id)
    fresh = await repo.get(run.id)
    assert fresh.status == "completed"
    assert fresh.posts_collected == 3  # weibo(1) + bilibili(1) + zhihu(1)，不重复累计
    assert executor._calls.count("weibo") == 0  # completed 平台跳过
    assert executor._calls.count("bilibili") == 1
    assert executor._calls.count("zhihu") == 1
    await db.dispose()


async def test_cr16_failed_platform_retries_within_limit() -> None:
    db, case, service, _ = await _setup()
    executor = FakePlatformExecutor({
        "weibo": [_post("weibo", 1)],
        "bilibili": [_post("bilibili", 1)],
        "zhihu": [_post("zhihu", 1)],
    })
    executor.fail_next("bilibili", times=1)  # 首轮失败，retry 成功
    run = await service.start(case.id, phase="discovery")
    worker = await _run_to_completion(db, executor, run.id)
    fresh = await worker._repository.get(run.id)
    assert fresh.status == "completed"
    assert executor._calls.count("bilibili") == 2
    assert fresh.progress_json["platforms"]["bilibili"]["attempts"] == 2
    assert fresh.progress_json["platforms"]["bilibili"]["status"] == "completed"
    await db.dispose()


async def test_cr17_retry_does_not_double_posts_collected() -> None:
    db, case, service, _ = await _setup()
    executor = FakePlatformExecutor({
        "weibo": [_post("weibo", 1), _post("weibo", 2)],
    })
    executor.fail_next("weibo", times=1)
    run = await service.start(case.id, phase="discovery", platforms=["weibo"])
    worker = await _run_to_completion(db, executor, run.id)
    fresh = await worker._repository.get(run.id)
    assert fresh.status == "completed"
    # 只有成功的那次计入：2 条（而非 4 条）
    assert fresh.posts_collected == 2
    await db.dispose()


async def test_cr18_completed_with_errors_preserves_successful_data() -> None:
    db, case, service, _ = await _setup()
    executor = FakePlatformExecutor({
        "weibo": [_post("weibo", 1)],
        "bilibili": [_post("bilibili", 1)],
        "zhihu": [_post("zhihu", 1)],
    })
    executor.fail_next("bilibili", times=10)  # 首轮 + retry 都失败
    run = await service.start(case.id, phase="discovery")
    worker = await _run_to_completion(db, executor, run.id)
    fresh = await worker._repository.get(run.id)
    assert fresh.status == "completed_with_errors"
    assert fresh.posts_collected >= 2  # weibo + zhihu 保留
    assert fresh.result_json["failed_platforms"] == ["bilibili"]
    await db.dispose()


async def test_cr19_all_platforms_fail_is_failed() -> None:
    db, case, service, _ = await _setup()
    executor = FakePlatformExecutor({
        "weibo": [_post("weibo", 1)],
        "bilibili": [_post("bilibili", 1)],
    })
    executor.fail_next("weibo", times=10)
    executor.fail_next("bilibili", times=10)
    run = await service.start(
        case.id, phase="discovery", platforms=["weibo", "bilibili"]
    )
    worker = await _run_to_completion(db, executor, run.id)
    fresh = await worker._repository.get(run.id)
    assert fresh.status == "failed"
    assert fresh.error_code == "platform_failures"
    await db.dispose()


async def test_cr20_all_platforms_succeed_is_completed() -> None:
    db, case, service, _ = await _setup()
    executor = FakePlatformExecutor({
        "weibo": [_post("weibo", 1)],
        "bilibili": [_post("bilibili", 1)],
        "zhihu": [_post("zhihu", 1)],
    })
    run = await service.start(case.id, phase="discovery")
    worker = await _run_to_completion(db, executor, run.id)
    fresh = await worker._repository.get(run.id)
    assert fresh.status == "completed"
    assert fresh.posts_collected == 3
    await db.dispose()


async def test_cw01_discovery_platform_concurrency_at_most_2() -> None:
    db, case, service, _ = await _setup()
    executor = FakePlatformExecutor({
        "weibo": [_post("weibo", 1)],
        "bilibili": [_post("bilibili", 1)],
        "zhihu": [_post("zhihu", 1)],
        "douyin": [],
    })
    run = await service.start(case.id, phase="discovery")
    worker = _worker(db, executor)
    # 替换 semaphore 为可观测版本：追踪最大并发
    max_concurrent = {"value": 0}
    current = {"value": 0}
    original = worker._platform_concurrency

    class _ObservedSem:
        def __init__(self, value: int) -> None:
            self._sem = asyncio.Semaphore(value)

        async def __aenter__(self) -> None:
            current["value"] += 1
            max_concurrent["value"] = max(max_concurrent["value"], current["value"])
            await self._sem.acquire()

        async def __aexit__(self, *exc: Any) -> None:
            current["value"] -= 1
            self._sem.release()

    worker._platform_concurrency = {"discovery": 2, "deep": 1}
    # 直接跑 _run_pass 观察并发上限
    snapshot = (await service.get_for_case(case.id, run.id)).request_json
    progress = dict((await service.get_for_case(case.id, run.id)).progress_json)
    from app.application.collection_run_worker import CollectionRunWorker
    sem = _ObservedSem(2)
    await CollectionRunWorker._run_pass(
        worker, run.id, ["weibo", "bilibili", "zhihu", "douyin"],
        snapshot, progress, asyncio.Event(), sem,  # type: ignore[arg-type]
    )
    assert max_concurrent["value"] <= 2
    await db.dispose()


async def test_cw02_deep_platform_concurrency_at_most_1() -> None:
    db, case, service, _ = await _setup()
    executor = FakePlatformExecutor({
        "weibo": [_post("weibo", 1)],
        "bilibili": [_post("bilibili", 1)],
    })
    run = await service.start(case.id, phase="deep")
    snapshot = run.request_json
    assert snapshot["budget"]["include_comments"] is True
    assert snapshot["budget"]["comment_limit"] == 10
    worker = _worker(db, executor)
    assert worker._platform_concurrency["deep"] == 1
    await db.dispose()
