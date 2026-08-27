"""Run a bounded real-crawler smoke test through the COIFESP adapter."""

from __future__ import annotations

import asyncio
import json

from app.application.ports.crawler import CrawlRequest
from app.bootstrap import ApplicationContainer
from app.core.config import get_settings


async def run() -> None:
    container = ApplicationContainer(get_settings())
    posts = await container.crawler.collect(
        CrawlRequest(
            topic="人工智能",
            platforms=["weibo", "bilibili"],
            time_range={"start": None, "end": None},
            limit_per_platform=1,
        )
    )
    print(
        json.dumps(
            {
                "count": len(posts),
                "platforms": [post["platform"] for post in posts],
                "ids_present": [bool(post["id"]) for post in posts],
                "timestamps_present": [bool(post["published_at"]) for post in posts],
                "all_real": all(post["is_demo"] is False for post in posts),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(run())
