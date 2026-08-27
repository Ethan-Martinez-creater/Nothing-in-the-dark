"""MonitorScheduler: 连续监测的独立调度 Worker (01).

与 GraphWorker 解耦，使用独立轮询循环，避免占用 Agent 运行队列：

- 调度阶段：扫描 enabled 监测，用 (monitor_id, scheduled_at) 唯一约束幂等
  生成到期执行（多 Worker 竞争时只有一个成功）。
- 执行阶段：原子领取执行（FOR UPDATE SKIP LOCKED + 租约），按平台增量采集
  （成功平台提交游标、失败平台保持旧游标并退避），汇总窗口统计后评估
  五类确定性告警。
- 分析触发：可选，通过 AgentRunService 创建分析运行，不直接调用 Agent。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from app.application.agent_service import AgentRunService
from app.application.ports.crawler import CrawlRequest, SocialCrawlerPort
from app.infrastructure.database.monitor_repository import MonitorRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.services import monitoring

logger = logging.getLogger(__name__)

_WINDOW_METRICS = ("post_count", "comment_count", "engagement_total")


class MonitorScheduler:
    def __init__(
        self,
        repository: MonitorRepository,
        social: SocialRepository,
        crawler: SocialCrawlerPort,
        agent_service: AgentRunService | None = None,
        *,
        worker_id: str = "local-monitor-worker",
        poll_interval_seconds: float = 5.0,
        lease_seconds: int = 600,
        overlap_seconds: int = 0,
        enabled: bool = True,
        max_concurrent_executions: int = 2,
    ) -> None:
        self._repository = repository
        self._social = social
        self._crawler = crawler
        self._agent_service = agent_service
        self._worker_id = worker_id
        self._poll_interval = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._overlap_seconds = overlap_seconds
        self._enabled = enabled
        self._max_concurrent = max_concurrent_executions
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        self._execution_tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        if not self._enabled:
            logger.info("MonitorScheduler disabled by configuration")
            return
        self._task = asyncio.create_task(
            self._loop(),
            name=f"monitor-scheduler:{self._worker_id}",
        )

    async def stop(self) -> None:
        self._stopping = True
        for task in list(self._execution_tasks.values()):
            task.cancel()
        if self._execution_tasks:
            await asyncio.gather(*self._execution_tasks.values(), return_exceptions=True)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive loop
                logger.exception("monitor scheduler tick failed")
            await asyncio.sleep(self._poll_interval)

    async def tick(self) -> str | None:
        """One scheduler pass: schedule due monitors, then claim/execute one."""
        await self._schedule_due()
        return await self._claim_and_execute()

    # ---- 调度阶段 --------------------------------------------------------

    async def _schedule_due(self) -> None:
        now = datetime.now(UTC)
        monitors = await self._repository.list_monitors(enabled=True)
        for monitor in monitors:
            try:
                last = await self._repository.get_latest_scheduled_at(monitor.id)
                next_at = monitoring.compute_next_scheduled_at(
                    schedule_type=monitor.schedule_type,
                    interval_seconds=monitor.interval_seconds,
                    cron=monitor.cron,
                    timezone=monitor.timezone,
                    last_scheduled_at=last,
                    now=now,
                )
                if next_at <= now:
                    await self._repository.create_execution(
                        monitor_id=monitor.id,
                        scheduled_at=next_at,
                    )
            except (ValueError, Exception) as exc:  # noqa: BLE001 - keep scheduling others
                logger.warning("monitor %s scheduling failed: %s", monitor.id, exc)

    async def _claim_and_execute(self) -> str | None:
        if len(self._execution_tasks) >= self._max_concurrent:
            return None
        execution = await self._repository.claim_execution(
            self._worker_id,
            self._lease_seconds,
        )
        if execution is None:
            return None
        task = asyncio.create_task(
            self._execute(execution.id),
            name=f"monitor-execution:{execution.id}",
        )
        self._execution_tasks[execution.id] = task
        task.add_done_callback(
            lambda completed, eid=execution.id: self._on_execution_done(eid, completed)
        )
        return execution.id

    def _on_execution_done(self, execution_id: str, task: asyncio.Task[None]) -> None:
        self._execution_tasks.pop(execution_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "monitor execution %s stopped with an error",
                execution_id,
                exc_info=(type(error), error, error.__traceback__),
            )

    # ---- 执行阶段 --------------------------------------------------------

    async def run_now(
        self,
        monitor_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        """创建一次 pending execution，由 Worker 领取执行。

        幂等：同一 idempotency_key 返回已有 execution，不重复创建。
        run-now 不直接执行，避免 HTTP 请求阻塞在真实采集上。
        """
        await self._repository.get_monitor(monitor_id)  # 404 for unknown monitor
        now = datetime.now(UTC)
        if idempotency_key:
            existing = await self._repository.get_execution_by_idempotency_key(
                monitor_id, idempotency_key
            )
            if existing is not None:
                return existing
        execution = await self._repository.create_execution(
            monitor_id=monitor_id,
            scheduled_at=now,
            idempotency_key=idempotency_key,
        )
        if execution is None:
            if idempotency_key:
                existing = await self._repository.get_execution_by_idempotency_key(
                    monitor_id, idempotency_key
                )
                if existing is not None:
                    return existing
            return await self._repository.get_execution_by_scheduled_at(monitor_id, now)
        return execution

    async def _execute(self, execution_id: str) -> None:
        execution = await self._repository.get_execution(execution_id)
        monitor = await self._repository.get_monitor(execution.monitor_id)
        now = datetime.now(UTC)
        platform_results: dict[str, Any] = {}
        all_posts: list[dict[str, object]] = []
        failed_platforms: list[str] = []

        query_spec = monitor.query_spec or {}
        topic = str(query_spec.get("topic") or monitor.name)

        # MON-P0-03：先确定并持久化 execution 级窗口，再采集。
        exec_start, exec_end, _ = await self._compute_execution_window(monitor, now)
        if not await self._repository.update_execution_if_owner(
            execution_id,
            self._worker_id,
            window_start=exec_start,
            window_end=exec_end,
        ):
            return

        # MON-P1-05：执行期间周期续租；一旦失去所有权就停止本地副作用。
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat_loop(execution_id, lease_lost))
        try:
            for platform in monitor.platforms:
                if lease_lost.is_set():
                    raise RuntimeError("monitor_execution_lease_lost")
                try:
                    cursor = await self._repository.get_cursor(monitor.id, platform)
                    window_start, window_end, _first = monitoring.compute_window(
                        schedule_type=monitor.schedule_type,
                        interval_seconds=monitor.interval_seconds,
                        cron=monitor.cron,
                        timezone=monitor.timezone,
                        lookback_seconds=monitor.lookback_seconds,
                        last_window_end=cursor.last_window_end if cursor else None,
                        now=now,
                        overlap_seconds=self._overlap_seconds,
                    )
                    keywords = self._platform_keywords(
                        query_spec.get("keywords"),
                        monitor.account_watchlist or [],
                        platform,
                    )
                    request = CrawlRequest(
                        topic=topic,
                        platforms=[platform],
                        time_range={
                            "start": window_start.isoformat(),
                            "end": window_end.isoformat(),
                        },
                        keywords={platform: keywords} if keywords else None,
                        cancel_event=lease_lost,
                    )
                    posts = await self._crawler.collect(request)
                    if lease_lost.is_set():
                        raise RuntimeError("monitor_execution_lease_lost")
                    posts = [p for p in posts if isinstance(p, dict)]
                    # 排除词：采集后过滤（排除词命中正文/标题的帖子）。
                    exclude = query_spec.get("exclude_keywords")
                    if isinstance(exclude, list) and exclude:
                        posts = self._filter_excluded(posts, exclude)
                    if posts:
                        await self._social.persist_batch(
                            case_id=monitor.case_id,
                            posts=posts,
                        )
                    if lease_lost.is_set():
                        raise RuntimeError("monitor_execution_lease_lost")
                    await self._repository.upsert_cursor(
                        monitor_id=monitor.id,
                        platform=platform,
                        cursor_payload={"last_window_end": window_end.isoformat()},
                        last_window_end=window_end,
                    )
                    stats = self._posts_stats(posts)
                    platform_results[platform] = {
                        "status": "ok",
                        "window_start": window_start.isoformat(),
                        "window_end": window_end.isoformat(),
                        **stats,
                    }
                    all_posts.extend(posts)
                except asyncio.CancelledError:
                    raise
                except RuntimeError as exc:
                    if str(exc) == "monitor_execution_lease_lost":
                        raise
                    failed_platforms.append(platform)
                    platform_results[platform] = {
                        "status": "failed",
                        "error": str(exc)[:300],
                    }
                    await self._repository.record_cursor_failure(monitor.id, platform)
                    logger.warning(
                        "monitor %s platform %s failed: %s",
                        monitor.id,
                        platform,
                        exc,
                    )
                except Exception as exc:  # noqa: BLE001 - partial success keeps going
                    failed_platforms.append(platform)
                    platform_results[platform] = {
                        "status": "failed",
                        "error": str(exc)[:300],
                    }
                    await self._repository.record_cursor_failure(monitor.id, platform)
                    logger.warning(
                        "monitor %s platform %s failed: %s",
                        monitor.id,
                        platform,
                        exc,
                    )

            if lease_lost.is_set():
                raise RuntimeError("monitor_execution_lease_lost")
            window = self._window_summary(all_posts, exec_start, exec_end)
            baseline = await self._build_baseline(monitor.id)
            alerts = await self._evaluate_alerts(monitor, window, baseline)

            # MON-P0-02：按成功/失败平台数量分别计算状态。
            success_count = len(monitor.platforms) - len(failed_platforms)
            if success_count == 0 and failed_platforms:
                status = "failed"
            elif failed_platforms:
                status = "partial"
            else:
                status = "succeeded"
            error_code = "platform_failed" if failed_platforms else None

            finished = await self._repository.finish_execution(
                execution_id,
                self._worker_id,
                status=status,
                platform_stats={
                    "platforms": platform_results,
                    "window": {
                        "start": exec_start.isoformat(),
                        "end": exec_end.isoformat(),
                    },
                    "totals": {
                        "post_count": window["post_count"],
                        "comment_count": window["comment_count"],
                        "engagement_total": window["engagement_total"],
                    },
                    "alerts_fired": len(alerts),
                },
                error_code=error_code,
            )
            if not finished:
                raise RuntimeError("monitor_execution_lease_lost")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._repository.finish_execution(
                execution_id,
                self._worker_id,
                status="failed",
                error_code=(
                    "lease_lost"
                    if str(exc) == "monitor_execution_lease_lost"
                    else "execution_error"
                ),
                platform_stats={"platforms": platform_results},
            )
            raise
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

        if alerts and self._agent_service is not None:
            await self._trigger_analysis(monitor, window, alerts, execution)

    async def _compute_execution_window(
        self,
        monitor: Any,
        now: datetime,
    ) -> tuple[Any, Any, bool]:
        """执行级窗口：取各平台游标中最早的 last_window_end 作为全局游标。"""
        cursors = await self._repository.list_cursors(monitor.id)
        last_ends = [c.last_window_end for c in cursors if c.last_window_end is not None]
        global_last = min(last_ends) if last_ends else None
        return monitoring.compute_window(
            schedule_type=monitor.schedule_type,
            interval_seconds=monitor.interval_seconds,
            cron=monitor.cron,
            timezone=monitor.timezone,
            lookback_seconds=monitor.lookback_seconds,
            last_window_end=global_last,
            now=now,
            overlap_seconds=self._overlap_seconds,
        )

    async def _heartbeat_loop(self, execution_id: str, lease_lost: asyncio.Event) -> None:
        """周期续租；租约被抢占时通知执行协程停止。"""
        interval = max(1.0, self._lease_seconds / 3.0)
        while True:
            await asyncio.sleep(interval)
            ok = await self._repository.refresh_execution_lease(
                execution_id, self._worker_id, self._lease_seconds
            )
            if not ok:
                lease_lost.set()
                return

    @staticmethod
    def _platform_keywords(
        raw_keywords: Any,
        watchlist: list[dict[str, object]],
        platform: str,
    ) -> list[str]:
        keywords = [str(value).strip() for value in raw_keywords or [] if str(value).strip()]
        for account in watchlist:
            account_platform = str(account.get("platform") or "").strip()
            if account_platform and account_platform != platform:
                continue
            for key in ("name", "handle", "native_id", "account_id"):
                value = str(account.get(key) or "").strip()
                if value:
                    keywords.append(value)
        return list(dict.fromkeys(keywords))

    @staticmethod
    def _filter_excluded(
        posts: list[dict[str, object]],
        exclude: list[str],
    ) -> list[dict[str, object]]:
        terms = [str(t).lower() for t in exclude if str(t).strip()]
        if not terms:
            return posts
        result: list[dict[str, object]] = []
        for post in posts:
            text = (str(post.get("content") or "") + " " + str(post.get("title") or "")).lower()
            if not any(term in text for term in terms):
                result.append(post)
        return result

    # ---- 统计与告警 ------------------------------------------------------

    @staticmethod
    def _posts_stats(posts: list[dict[str, object]]) -> dict[str, Any]:
        comments = 0
        engagement = 0
        for post in posts:
            raw_comments = post.get("comments")
            if isinstance(raw_comments, list):
                comments += len(raw_comments)
            try:
                engagement += int(post.get("engagement") or 0)
            except (TypeError, ValueError):
                pass
        return {
            "post_count": len(posts),
            "comment_count": comments,
            "engagement_total": engagement,
        }

    @staticmethod
    def _window_summary(
        posts: list[dict[str, object]],
        window_start: Any,
        window_end: Any,
    ) -> dict[str, Any]:
        accounts: list[dict[str, object]] = []
        comments = 0
        engagement = 0
        for post in posts:
            raw_comments = post.get("comments")
            if isinstance(raw_comments, list):
                comments += len(raw_comments)
            try:
                engagement += int(post.get("engagement") or 0)
            except (TypeError, ValueError):
                pass
            accounts.append(
                {
                    "id": post.get("author_id") or post.get("native_id") or "",
                    "name": post.get("author") or "",
                    "platform": post.get("platform") or "",
                }
            )
        return {
            "post_count": len(posts),
            "comment_count": comments,
            "engagement_total": engagement,
            "accounts": accounts,
            "_window": {
                "start": window_start.isoformat() if window_start else None,
                "end": window_end.isoformat() if window_end else None,
            },
        }

    async def _build_baseline(self, monitor_id: str) -> dict[str, Any]:
        recent = await self._repository.list_recent_executions(monitor_id, limit=20)
        baseline: dict[str, Any] = {
            "post_count": 0,
            "comment_count": 0,
            "engagement_total": 0,
            "history": {"post_count": [], "comment_count": [], "engagement_total": []},
        }
        if not recent:
            return baseline
        # recent 已按 scheduled_at 倒序，第一个是上一个窗口。
        prev = recent[0].platform_stats or {}
        totals = prev.get("totals", {})
        for metric in _WINDOW_METRICS:
            baseline[metric] = totals.get(metric, 0)
        # 历史序列按时间正序（旧 -> 新）。
        for execution in reversed(recent):
            stats = execution.platform_stats or {}
            totals = stats.get("totals", {})
            for metric in _WINDOW_METRICS:
                baseline["history"][metric].append(totals.get(metric, 0))
        return baseline

    async def _evaluate_alerts(
        self,
        monitor: Any,
        window: dict[str, Any],
        baseline: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rules = await self._repository.list_rules(monitor.id)
        fired: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for rule in rules:
            if not rule.enabled:
                continue
            hit = monitoring.evaluate_rule(
                rule_type=rule.rule_type,
                parameters=rule.parameters or {},
                severity=rule.severity,
                window=window,
                baseline=baseline,
                account_watchlist=monitor.account_watchlist or [],
                narratives=window.get("narratives", []),
            )
            if hit is None:
                continue
            bucket = monitoring.cooldown_bucket(now, rule.cooldown_seconds)
            await self._repository.upsert_alert_occurrence(
                monitor_id=monitor.id,
                rule_id=rule.id,
                fingerprint=hit.fingerprint,
                cooldown_bucket=bucket,
                severity=hit.severity,
                explanation=hit.explanation,
                metric_snapshot=hit.metric_snapshot,
                evidence_refs=hit.evidence_refs,
            )
            fired.append(hit.to_dict())
        return fired

    async def _trigger_analysis(
        self,
        monitor: Any,
        window: dict[str, Any],
        alerts: list[dict[str, Any]],
        execution: Any,
    ) -> None:
        policy = monitor.analysis_policy or {}
        if not policy.get("trigger_analysis"):
            return
        if self._agent_service is None:
            return
        approve_crawl = bool(policy.get("approve_crawl", False))
        alert_text = "；".join(a["explanation"] for a in alerts[:3])
        content = (
            f"[监测 {monitor.name}] 新窗口采集到 {window['post_count']} 条帖子、"
            f"{window['comment_count']} 条评论。告警：{alert_text or '无'}"
        )
        run = await self._agent_service.start(
            case_id=monitor.case_id,
            content=content,
            approve_crawl=approve_crawl,
        )
        if run is not None:
            await self._repository.update_execution(
                execution.id,
                run_id=getattr(run, "id", None),
            )
