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
    # 每平台 aggregate 上游抓取上限（Discovery 场景按平台 Aggregate
    # Budget 显式传入；None 时保持 legacy 语义，由 fetch_limit_for 决定）。
    upstream_limit_per_platform: int | None = None
    # 是否抓取评论；None 时回退适配器默认（legacy 语义）。
    include_comments: bool | None = None
    # 每平台的检索关键词组（LLM 检索优化产出）；缺省回退 [topic]。
    keywords: dict[str, list[str]] | None = None
    cancel_event: asyncio.Event | None = None
    # 输出子目录名（run 级进度扫描用）。缺省时 adapter 随机生成 uuid，
    # 这样 worker 无法在采集运行中定位输出目录来统计实时进度。
    output_root_name: str | None = None


class SocialCrawlerPort(Protocol):
    async def collect(self, request: CrawlRequest) -> list[dict[str, object]]:
        """Collect and normalize a minimal set of social posts."""

