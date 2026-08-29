"""M3.7: crawl 工具接入 Active Collection Definition 的集成验证。

- 有 active definition：keywords 来自定义投影，输出附带 collection_definition
  审计引用（id + version）；
- 无 active definition：回退既有 generate_platform_keywords 路径，无引用字段；
- approval/sandbox 契约不变（sandbox stub 正常执行）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.harness.tool_factory import build_tool_registry
from app.services.crawl_coverage import CrawlRequest


class RecordingCrawler:
    def __init__(self) -> None:
        self.requests: list[CrawlRequest] = []

    async def collect(self, request: Any) -> list[dict[str, object]]:
        self.requests.append(request)
        await asyncio.sleep(0.01)
        return [
            {"id": f"{request.platforms[0]}-1", "platform": request.platforms[0], "content": "x"}
        ]


class CrawlerSandboxStub:
    def __init__(self, crawler: Any) -> None:
        self._crawler = crawler

    async def execute(self, *, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        request = CrawlRequest(
            topic=str(payload.get("topic") or ""),
            platforms=list(payload.get("platforms") or []),
            time_range=dict(payload.get("time_range") or {}),
            limit_per_platform=int(payload.get("limit_per_platform") or 150),
            per_day_limit=int(payload.get("per_day_limit") or 150),
            comment_limit=int(payload.get("comment_limit") or 10),
            keywords=dict(payload.get("keywords") or {}),
        )
        posts = await self._crawler.collect(request)
        return {"ok": True, "posts": posts, "platforms": request.platforms}


class FakeActiveDefinition:
    id = "col-def-1"
    version = 3
    platform_queries = {"weibo": ["召回"], "bilibili": ["自燃"]}
    exclusions: list[str] = []


class FakeCollectionService:
    """fake：返回预置 active definition；keywords_for 与真实 service 同签名。"""

    def __init__(self, active: FakeActiveDefinition | None) -> None:
        self._active = active
        self.requested_case_ids: list[str] = []

    async def get_active(self, case_id: str) -> FakeActiveDefinition | None:
        self.requested_case_ids.append(case_id)
        return self._active

    def keywords_for(
        self,
        definition: FakeActiveDefinition,
        requested_platforms: list[str],
        fallback_topic: str,
    ) -> dict[str, list[str]]:
        queries = dict(definition.platform_queries)
        return {
            platform: list(queries.get(platform, [fallback_topic]))
            for platform in requested_platforms
        }


async def test_crawl_uses_active_definition_and_audits_reference() -> None:
    crawler = RecordingCrawler()
    service = FakeCollectionService(FakeActiveDefinition())
    registry = build_tool_registry(crawler, llm=None, collection_service=service)
    registry.set_sandbox_executor(CrawlerSandboxStub(crawler))

    result = await registry.invoke(
        "collect_social_posts",
        {
            "case_id": "case-1",
            "topic": "新能源汽车",
            "platforms": ["weibo", "bilibili"],
            "time_range": {},
        },
    )

    assert service.requested_case_ids == ["case-1"]
    # 关键词来自 active definition（crawl 逐平台调用 sandbox，合并各平台请求）
    keywords_by_platform: dict[str, list[str]] = {}
    for request in crawler.requests:
        keywords_by_platform.update(request.keywords)
    assert keywords_by_platform == {"weibo": ["召回"], "bilibili": ["自燃"]}
    # 输出附带审计引用
    assert result["collection_definition"] == {"id": "col-def-1", "version": 3}


async def test_crawl_falls_back_without_active_definition() -> None:
    crawler = RecordingCrawler()
    service = FakeCollectionService(None)
    registry = build_tool_registry(crawler, llm=None, collection_service=service)
    registry.set_sandbox_executor(CrawlerSandboxStub(crawler))

    result = await registry.invoke(
        "collect_social_posts",
        {
            "case_id": "case-1",
            "topic": "新能源汽车",
            "platforms": ["weibo"],
            "time_range": {},
        },
    )

    # 无 active：回退 generate_platform_keywords（llm=None → 每平台 [topic]）
    assert crawler.requests[0].keywords == {"weibo": ["新能源汽车"]}
    assert "collection_definition" not in result


async def test_crawl_definition_subset_platform_falls_back_to_topic() -> None:
    crawler = RecordingCrawler()
    service = FakeCollectionService(FakeActiveDefinition())
    registry = build_tool_registry(crawler, llm=None, collection_service=service)
    registry.set_sandbox_executor(CrawlerSandboxStub(crawler))

    # 定义只覆盖 weibo/bilibili；请求包含知乎 → 知乎回退 topic，不静默丢平台
    await registry.invoke(
        "collect_social_posts",
        {
            "case_id": "case-1",
            "topic": "新能源汽车",
            "platforms": ["weibo", "zhihu"],
            "time_range": {},
        },
    )
    keywords_by_platform: dict[str, list[str]] = {}
    for request in crawler.requests:
        keywords_by_platform.update(request.keywords)
    assert keywords_by_platform == {
        "weibo": ["召回"],
        "zhihu": ["新能源汽车"],
    }
