"""Recoverable background worker executing approved CollectionRuns.

CollectionRunWorker 生命周期：start / stop / loop / tick / execute。

执行模型（对应执行方案 AC9）：

- ``claim_next`` 原子领取 queued 或租约过期的 running run（FOR UPDATE
  SKIP LOCKED，SQLite 兼容）。
- 执行期间独立 heartbeat task（lease/3 间隔）续租并检查取消请求；丢租即
  ``cancel_event`` 触发，终止平台任务与 MediaCrawler 子进程树（INV-2）。
- 平台级有界并发：Discovery <= 2、Deep <= 1；同时受全局
  CrawlCapacityLimiter 约束。
- 平台任务只做浏览器 I/O（沙箱采集），结果经 asyncio.Queue 交给
  single-writer coordinator 顺序过滤/去重/持久化/checkpoint（INV-4、
  文档 39/40 节）。
- 平台失败就地隔离：首轮失败平台在全部平台首轮结束后延迟重试
  （每平台最多 2 次尝试）；仍失败按细则记录，成功平台数据保留。
- Worker 重启后从 progress checkpoint 恢复：completed 平台跳过，
  其余重新排队执行（文档 29 节）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from app.application.ports.crawler import CrawlRequest
from app.harness.collection_platform_executor import CollectionPlatformExecutor
from app.infrastructure.database.collection_run_repository import CollectionRunRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.services.collection_filters import apply_collection_exclusions
from app.services.crawl_coverage import apply_coverage

logger = logging.getLogger(__name__)

PLATFORM_MAX_ATTEMPTS = 2


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class CollectionRunWorker:
    def __init__(
        self,
        repository: CollectionRunRepository,
        platform_executor: CollectionPlatformExecutor,
        social: SocialRepository,
        *,
        worker_id: str = "local-collection-worker",
        poll_interval_seconds: float = 2.0,
        lease_seconds: int = 60,
        enabled: bool = True,
        platform_concurrency_discovery: int = 2,
        platform_concurrency_deep: int = 1,
        telemetry: Any | None = None,
    ) -> None:
        self._repository = repository
        self._executor = platform_executor
        self._social = social
        self._worker_id = worker_id
        self._poll_interval = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._enabled = enabled
        self._platform_concurrency = {
            "discovery": max(1, platform_concurrency_discovery),
            "deep": max(1, platform_concurrency_deep),
        }
        self._telemetry = telemetry
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        self._active: set[asyncio.Task[Any]] = set()

    # ---------------- lifecycle ----------------

    async def start(self) -> None:
        if not self._enabled:
            logger.info("CollectionRunWorker disabled")
            return
        self._task = asyncio.create_task(
            self._loop(), name=f"collection-worker:{self._worker_id}"
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # 停止 active platform tasks：cancel 传播到沙箱子进程树。
        active = list(self._active)
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
            self._active.clear()

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("collection worker tick failed")
            await asyncio.sleep(self._poll_interval)

    async def tick(self) -> str | None:
        # 收敛过期租约且已请求取消/耗尽 claim 次数的残留 run（避免永久 running）
        await self._repository.recover_expired(
            self._worker_id, self._lease_seconds
        )
        run = await self._repository.claim_next(self._worker_id, self._lease_seconds)
        if run is None:
            return None
        task = asyncio.create_task(self._execute(run.id))
        self._active.add(task)
        task.add_done_callback(self._active.discard)
        return run.id

    # ---------------- run execution ----------------

    async def _execute(self, run_id: str) -> None:
        started = time.perf_counter()
        run = await self._repository.get(run_id)
        snapshot = dict(run.request_json or {})
        progress = self._normalize_progress(run, snapshot)
        phase = str(snapshot.get("phase") or "discovery")
        cancel_event = asyncio.Event()
        hb_task = asyncio.create_task(
            self._heartbeat_loop(run_id, cancel_event)
        )
        try:
            sem = asyncio.Semaphore(self._platform_concurrency.get(phase, 2))
            todo = self._recover_todo(snapshot, progress)
            if todo:
                await self._run_pass(
                    run_id, todo, snapshot, progress, cancel_event, sem
                )
                # deferred retry：首轮全部结束后，重试失败且未达上限的平台
                if not cancel_event.is_set():
                    retry = [
                        platform
                        for platform in todo
                        if self._platform_status(progress, platform) == "failed"
                        and self._platform_attempts(progress, platform)
                        < PLATFORM_MAX_ATTEMPTS
                    ]
                    if retry:
                        self._metric_increment(
                            "collection.retry_count", value=len(retry), phase=phase
                        )
                        logger.warning(
                            "collection run %s retry pass for platforms %s",
                            run_id, retry,
                        )
                        await self._run_pass(
                            run_id, retry, snapshot, progress, cancel_event, sem
                        )
            terminal = self._terminal_status(snapshot, progress, cancel_event)
            result = self._build_result(snapshot, progress, terminal)
            await self._mark_terminal(run_id, terminal, result)
        finally:
            hb_task.cancel()
            await asyncio.gather(hb_task, return_exceptions=True)
        total_ms = int((time.perf_counter() - started) * 1000)
        self._metric_observe(
            "collection.total_duration_ms", total_ms, phase=phase
        )

    async def _heartbeat_loop(
        self, run_id: str, cancel_event: asyncio.Event
    ) -> None:
        interval = max(0.2, self._lease_seconds / 3.0)
        try:
            while True:
                await asyncio.sleep(interval)
                owns, cancel_requested = await self._repository.heartbeat(
                    run_id, self._worker_id, self._lease_seconds
                )
                if not owns:
                    logger.warning("collection run %s lost its lease", run_id)
                    self._metric_increment("collection.lease_lost")
                    cancel_event.set()
                    return
                if cancel_requested:
                    cancel_event.set()
        except asyncio.CancelledError:
            pass

    async def _run_pass(
        self,
        run_id: str,
        platforms: list[str],
        snapshot: dict[str, Any],
        progress: dict[str, Any],
        cancel_event: asyncio.Event,
        sem: asyncio.Semaphore,
    ) -> None:
        """一轮平台执行：浏览器 I/O 并发，DB 写全部由 single-writer 顺序做。"""
        queue: asyncio.Queue[tuple[str, str, int, Any]] = asyncio.Queue()
        pending = len(platforms)
        phase = str(snapshot.get("phase") or "discovery")

        async def platform_task(platform: str) -> None:
            attempt = self._platform_attempts(progress, platform) + 1
            await queue.put(("started", platform, attempt, None))
            try:
                async with sem:
                    if cancel_event.is_set():
                        await queue.put(("cancelled", platform, attempt, None))
                        return
                    platform_started = time.perf_counter()
                    posts = await self._executor.run_platform(
                        platform,
                        snapshot,
                        cancel_event=cancel_event,
                        run_id=run_id,
                        tool_call_id=f"collection-run:{run_id}:{platform}",
                    )
                    duration_ms = int(
                        (time.perf_counter() - platform_started) * 1000
                    )
                    self._metric_observe(
                        "collection.platform_duration_ms",
                        duration_ms,
                        phase=phase,
                        platform=platform,
                    )
                    await queue.put(("result", platform, attempt, posts))
            except asyncio.CancelledError:
                # 任务已取消：put_nowait 保证消息仍入队（queue 无上限），
                # 避免 coordinator 在 queue.get() 永久等待。
                try:
                    queue.put_nowait(("cancelled", platform, attempt, None))
                except asyncio.QueueFull:
                    pass
            except Exception as exc:  # noqa: BLE001 - 平台失败隔离
                await queue.put(
                    (
                        "error",
                        platform,
                        attempt,
                        str(exc).strip()[:400] or type(exc).__name__,
                    )
                )

        tasks = [asyncio.create_task(platform_task(platform)) for platform in platforms]
        try:
            while pending > 0:
                # 优先消费队列中已有的消息；队列空且所有平台任务已结束才
                # 退出（消息可能因取消中断未收齐，剩余平台交由恢复逻辑）。
                try:
                    kind, platform, attempt, data = queue.get_nowait()
                except asyncio.QueueEmpty:
                    if all(task.done() for task in tasks):
                        logger.warning(
                            "collection run %s platform tasks ended early "
                            "(pending=%d), leaving to recovery",
                            run_id, pending,
                        )
                        break
                    kind, platform, attempt, data = await queue.get()
                if kind == "started":
                    # single-writer：只有 coordinator 写 checkpoint/progress
                    self._mark_platform_running(progress, platform, attempt)
                    await self._flush_progress(run_id, progress)
                    continue
                pending -= 1
                if kind == "result":
                    kept, comments = await self._ingest_platform(
                        run_id, platform, attempt, list(data or []), snapshot
                    )
                    self._mark_platform_completed(
                        progress, platform, attempt, kept, comments
                    )
                    await self._flush_progress(run_id, progress)
                elif kind == "error":
                    self._mark_platform_failed(progress, platform, attempt, data)
                    await self._flush_progress(run_id, progress)
                    self._metric_increment(
                        "collection.platform_failures",
                        phase=phase,
                        platform=platform,
                    )
                elif kind == "cancelled":
                    self._mark_platform_cancelled(progress, platform, attempt)
                    await self._flush_progress(run_id, progress)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---------------- single-writer ingest ----------------

    async def _ingest_platform(
        self,
        run_id: str,
        platform: str,
        attempt: int,
        posts: list[dict[str, Any]],
        snapshot: dict[str, Any],
    ) -> tuple[int, int]:
        """平台结果立即过滤、去重、覆盖采样并持久化（文档 37 节）。"""
        budget = snapshot.get("budget") or {}
        exclusions = snapshot.get("exclusions") or []
        request = CrawlRequest(
            topic=str(snapshot.get("topic") or ""),
            platforms=[platform],
            time_range=dict(snapshot.get("time_range") or {}),
            limit_per_platform=int(budget.get("limit_per_platform") or 150),
            per_day_limit=int(budget.get("per_day_limit") or 150),
            comment_limit=int(budget.get("comment_limit") or 0),
            upstream_limit_per_platform=budget.get("upstream_limit_per_platform"),
            include_comments=bool(budget.get("include_comments", False)),
        )
        filtered, _ = apply_collection_exclusions(posts, exclusions)
        coverage = apply_coverage(filtered, request)
        kept_posts = coverage.posts
        comments = sum(
            len(post.get("comments") or []) for post in kept_posts
        )
        if kept_posts:
            await self._social.persist_batch(
                case_id=str(snapshot.get("case_id") or ""),
                posts=kept_posts,
            )
            phase = str(snapshot.get("phase") or "discovery")
            self._metric_increment(
                "collection.posts_persisted",
                value=len(kept_posts),
                phase=phase,
                platform=platform,
            )
            self._metric_increment(
                "collection.comments_persisted",
                value=comments,
                phase=phase,
                platform=platform,
            )
        return len(kept_posts), comments

    # ---------------- progress helpers ----------------

    @staticmethod
    def _normalize_progress(
        run: Any, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        progress = dict(run.progress_json or {})
        platforms = dict(progress.get("platforms") or {})
        for platform in snapshot.get("platforms") or []:
            platforms.setdefault(
                platform,
                {
                    "status": "queued",
                    "attempts": 0,
                    "posts_collected": 0,
                    "comments_collected": 0,
                    "started_at": None,
                    "completed_at": None,
                    "error_code": None,
                    "error_message": None,
                },
            )
        progress["platforms"] = platforms
        progress["total_platforms"] = len(snapshot.get("platforms") or [])
        progress.setdefault("completed_platforms", 0)
        return progress

    def _recover_todo(
        self, snapshot: dict[str, Any], progress: dict[str, Any]
    ) -> list[str]:
        """恢复 checkpoint：completed 跳过；failed 达上限保持 failed。"""
        todo: list[str] = []
        for platform in snapshot.get("platforms") or []:
            status = self._platform_status(progress, platform)
            attempts = self._platform_attempts(progress, platform)
            if status == "completed":
                continue
            if status == "failed" and attempts >= PLATFORM_MAX_ATTEMPTS:
                continue
            todo.append(platform)
        return todo

    @staticmethod
    def _platform_state(
        progress: dict[str, Any], platform: str
    ) -> dict[str, Any]:
        return (progress.get("platforms") or {}).get(platform) or {}

    @classmethod
    def _platform_status(
        cls, progress: dict[str, Any], platform: str
    ) -> str:
        return cls._platform_state(progress, platform).get("status") or "queued"

    @classmethod
    def _platform_attempts(
        cls, progress: dict[str, Any], platform: str
    ) -> int:
        return int(cls._platform_state(progress, platform).get("attempts") or 0)

    def _mark_platform_running(
        self, progress: dict[str, Any], platform: str, attempt: int
    ) -> None:
        state = self._platform_state(progress, platform)
        state["status"] = "running"
        state["attempts"] = attempt
        if not state.get("started_at"):
            state["started_at"] = _now_iso()
        state["error_code"] = None
        state["error_message"] = None

    def _mark_platform_completed(
        self,
        progress: dict[str, Any],
        platform: str,
        attempt: int,
        posts: int,
        comments: int,
    ) -> None:
        state = self._platform_state(progress, platform)
        state["status"] = "completed"
        state["attempts"] = attempt
        state["posts_collected"] = posts
        state["comments_collected"] = comments
        state["completed_at"] = _now_iso()
        self._recompute_totals(progress)

    def _mark_platform_failed(
        self, progress: dict[str, Any], platform: str, attempt: int, error: str
    ) -> None:
        state = self._platform_state(progress, platform)
        state["status"] = "failed"
        state["attempts"] = attempt
        state["error_code"] = "platform_failed"
        state["error_message"] = error
        state["completed_at"] = _now_iso()
        self._recompute_totals(progress)

    def _mark_platform_cancelled(
        self, progress: dict[str, Any], platform: str, attempt: int
    ) -> None:
        state = self._platform_state(progress, platform)
        state["status"] = "cancelled"
        state["attempts"] = attempt
        state["completed_at"] = _now_iso()
        self._recompute_totals(progress)

    def _recompute_totals(self, progress: dict[str, Any]) -> dict[str, Any]:
        platforms = progress.get("platforms") or {}
        completed = [
            platform
            for platform, state in platforms.items()
            if (state or {}).get("status") == "completed"
        ]
        progress["completed_platforms"] = len(completed)
        progress["posts_collected"] = sum(
            int((platforms[p] or {}).get("posts_collected") or 0)
            for p in completed
        )
        progress["comments_collected"] = sum(
            int((platforms[p] or {}).get("comments_collected") or 0)
            for p in completed
        )
        return progress

    async def _flush_progress(
        self, run_id: str, progress: dict[str, Any]
    ) -> None:
        await self._repository.update_progress_if_owner(
            run_id,
            self._worker_id,
            progress_json=progress,
            posts_collected=int(progress.get("posts_collected") or 0),
            comments_collected=int(progress.get("comments_collected") or 0),
        )

    # ---------------- terminal ----------------

    def _terminal_status(
        self,
        snapshot: dict[str, Any],
        progress: dict[str, Any],
        cancel_event: asyncio.Event,
    ) -> str:
        statuses = [
            self._platform_status(progress, platform)
            for platform in snapshot.get("platforms") or []
        ]
        if cancel_event.is_set() or "cancelled" in statuses:
            return "cancelled"
        completed = statuses.count("completed")
        failed = statuses.count("failed")
        total = len(statuses)
        if total and completed == total:
            return "completed"
        if total and failed == total:
            return "failed"
        return "completed_with_errors"

    def _build_result(
        self,
        snapshot: dict[str, Any],
        progress: dict[str, Any],
        terminal: str,
    ) -> dict[str, Any]:
        platforms = {
            platform: self._platform_state(progress, platform)
            for platform in snapshot.get("platforms") or []
        }
        failed_platforms = [
            platform
            for platform, state in platforms.items()
            if (state or {}).get("status") == "failed"
        ]
        error_message = None
        if failed_platforms:
            error_message = "; ".join(
                f"{platform}: {platforms[platform].get('error_message')}"
                for platform in failed_platforms
            )
        return {
            "phase": snapshot.get("phase"),
            "platforms": platforms,
            "completed_platforms": int(progress.get("completed_platforms") or 0),
            "failed_platforms": failed_platforms,
            "posts_collected": int(progress.get("posts_collected") or 0),
            "comments_collected": int(progress.get("comments_collected") or 0),
            "error_code": (
                "platform_failures" if failed_platforms else None
            ),
            "error_message": error_message,
        }

    async def _mark_terminal(
        self, run_id: str, terminal: str, result: dict[str, Any]
    ) -> None:
        if terminal == "completed":
            await self._repository.mark_completed_if_owner(
                run_id, self._worker_id, result
            )
        elif terminal == "completed_with_errors":
            await self._repository.mark_completed_with_errors_if_owner(
                run_id, self._worker_id, result
            )
        elif terminal == "cancelled":
            await self._repository.mark_cancelled_if_owner(
                run_id, self._worker_id, result
            )
            self._metric_increment("collection.cancelled")
        else:
            await self._repository.mark_failed_if_owner(
                run_id, self._worker_id, result
            )

    # ---------------- telemetry ----------------

    def _metric_increment(
        self, name: str, value: int = 1, **labels: str
    ) -> None:
        if self._telemetry is None:
            return
        try:
            self._telemetry.metrics.increment(
                name, value=value, labels=labels or None
            )
        except Exception:
            pass

    def _metric_observe(
        self, name: str, value: float, **labels: str
    ) -> None:
        if self._telemetry is None:
            return
        try:
            self._telemetry.metrics.observe(
                name, value=float(value), labels=labels or None
            )
        except Exception:
            pass
