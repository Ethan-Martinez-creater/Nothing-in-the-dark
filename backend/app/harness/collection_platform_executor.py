"""Sandboxed platform execution for background collection runs.

CollectionRunWorker 不得裸跑 MediaCrawler（M15）：平台级执行必须继续经过
ToolRegistry -> SandboxedToolExecutor -> internal collect_social_posts
capability -> MediaCrawlerAdapter，保留 restricted process / egress policy /
cancel propagation / process-tree termination / audit / timeout policy。

每个平台一次 sandbox 调用（一次 MediaCrawler process，多关键词逗号分隔），
受 run 内平台并发（discovery<=2 / deep<=1）与全局 CrawlCapacityLimiter 约束。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.application.collection_capacity import CrawlCapacityLimiter
from app.harness.tools import ToolRegistry


class CollectionPlatformExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        capacity_limiter: CrawlCapacityLimiter | None = None,
    ) -> None:
        self._registry = registry
        self._capacity = capacity_limiter

    async def run_platform(
        self,
        platform: str,
        snapshot: dict[str, Any],
        *,
        cancel_event: asyncio.Event | None = None,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        output_root_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """执行一个平台：从 immutable snapshot 构造 payload 并跑沙箱采集。"""
        budget = snapshot.get("budget") or {}
        keywords = dict(snapshot.get("keywords") or {})
        topic = str(snapshot.get("topic") or "")
        payload: dict[str, Any] = {
            "topic": topic,
            "platforms": [platform],
            "time_range": dict(snapshot.get("time_range") or {}),
            "limit_per_platform": int(budget.get("limit_per_platform") or 150),
            "per_day_limit": int(budget.get("per_day_limit") or 150),
            "comment_limit": int(budget.get("comment_limit") or 0),
            "upstream_limit_per_platform": budget.get("upstream_limit_per_platform"),
            "include_comments": bool(budget.get("include_comments", False)),
            "keywords": {
                platform: list(keywords.get(platform) or [topic]),
            },
            "output_root_name": output_root_name,
        }
        if self._capacity is not None:
            await self._capacity.acquire(cancel_event=cancel_event)
        acquired = self._capacity is not None
        try:
            external = await self._registry.run_external_tool(
                "collect_social_posts",
                payload,
                cancel_event=cancel_event,
                run_id=run_id,
                tool_call_id=tool_call_id,
            )
        finally:
            if acquired:
                self._capacity.release()
        return list(external.get("posts") or [])
