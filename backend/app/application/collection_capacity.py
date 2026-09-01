"""System-wide MediaCrawler process capacity limiter.

全局同时活跃的 MediaCrawler browser process 上限（默认
MEDIACRAWLER_GLOBAL_CONCURRENCY=2）。由 ApplicationContainer 创建单例，
CollectionRunWorker（CollectionPlatformExecutor）与 MonitorScheduler 在
执行真实采集前 acquire()，从系统级保证浏览器进程数不失控。
"""

from __future__ import annotations

import asyncio


class CrawlCapacityLimiter:
    def __init__(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._semaphore = asyncio.Semaphore(self._limit)

    @property
    def limit(self) -> int:
        return self._limit

    async def acquire(self, cancel_event: asyncio.Event | None = None) -> None:
        """获取容量；传入 cancel_event 时等待可被取消，避免平台任务在全局
        容量上阻塞时无法响应取消/租约丢失。"""
        if cancel_event is None:
            await self._semaphore.acquire()
            return
        acquire_task = asyncio.create_task(self._semaphore.acquire())
        cancel_task = asyncio.create_task(cancel_event.wait())
        try:
            done, _ = await asyncio.wait(
                {acquire_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if acquire_task in done:
                return
            acquire_task.cancel()
            await asyncio.gather(acquire_task, return_exceptions=True)
            raise asyncio.CancelledError()
        finally:
            for task in (acquire_task, cancel_task):
                if not task.done():
                    task.cancel()

    def release(self) -> None:
        self._semaphore.release()
