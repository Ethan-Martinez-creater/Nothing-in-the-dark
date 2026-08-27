from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class CrawlRequest:
    topic: str
    platforms: list[str]
    time_range: dict[str, str | None]
    limit_per_platform: int = 150
    # 时间连续覆盖：每个自然日、每平台最多保留这么多条（过滤排序后）。
    per_day_limit: int = 150
    # 每帖/每视频评论保留上限（过滤排序后的前 N 条）。
    comment_limit: int = 10
    # 每平台的检索关键词组（LLM 检索优化产出）；缺省回退 [topic]。
    keywords: dict[str, list[str]] | None = None
    cancel_event: asyncio.Event | None = None


class SocialCrawlerPort(Protocol):
    async def collect(self, request: CrawlRequest) -> list[dict[str, object]]:
        """Collect and normalize a minimal set of social posts."""

