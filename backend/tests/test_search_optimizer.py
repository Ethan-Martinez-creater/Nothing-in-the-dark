"""Search optimizer: LLM keyword generation / query rewrite with fallbacks."""

from __future__ import annotations

import json

from app.harness.search_optimizer import (
    generate_platform_keywords,
    rewrite_search_query,
)
from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse


class StubGateway(LLMGateway):
    def __init__(self, content: str) -> None:
        self._content = content
        self.last_user: str | None = None

    @property
    def configured(self) -> bool:
        return True

    async def complete(self, *, messages: list[LLMMessage], route=None, **kw):
        self.last_user = messages[-1].content
        return LLMResponse(
            message=LLMMessage(role="assistant", content=self._content),
            model="fake",
        )


async def test_generates_per_platform_keyword_groups() -> None:
    gateway = StubGateway(
        json.dumps(
            {
                "weibo": [["暴雨 泄洪", "水库泄洪 谣言"], ["泄洪 官方回应"]],
                "bilibili": [["暴雨水库泄洪事件", "谣言时间线"]],
            }
        )
    )
    result = await generate_platform_keywords(
        gateway, "暴雨后水库泄洪谣言", ["weibo", "bilibili"]
    )
    assert result["weibo"] == ["暴雨 泄洪 水库泄洪 谣言", "泄洪 官方回应"]
    assert result["bilibili"] == ["暴雨水库泄洪事件 谣言时间线"]
    # 平台画像应注入 prompt
    assert "微博" in gateway.last_user
    assert '"weibo"' in gateway.last_user
    assert "不要输出 platform/keywords 包装层" in gateway.last_user


async def test_rejects_legacy_wrapped_keyword_shape() -> None:
    gateway = StubGateway(
        json.dumps({"platform": "weibo", "keywords": [["事件", "回应"]]})
    )
    result = await generate_platform_keywords(gateway, "主题", ["weibo"])
    assert result == {"weibo": ["主题"]}


async def test_ignores_unknown_platforms() -> None:
    gateway = StubGateway(json.dumps({"douyin": [["未知平台词"]]}))
    result = await generate_platform_keywords(gateway, "主题", ["weibo"])
    assert result == {"weibo": ["主题"]}


async def test_falls_back_without_llm_or_on_bad_json() -> None:
    fallback = await generate_platform_keywords(None, "主题", ["weibo", "zhihu"])
    assert fallback == {"weibo": ["主题"], "zhihu": ["主题"]}

    bad = StubGateway("这不是 JSON")
    result = await generate_platform_keywords(bad, "主题", ["weibo"])
    assert result == {"weibo": ["主题"]}


async def test_rewrite_query_returns_rewritten_text() -> None:
    gateway = StubGateway("辟谣 澄清 官方通报 时间线 不实信息")
    rewritten = await rewrite_search_query(gateway, "辟谣时间线")
    assert rewritten == "辟谣 澄清 官方通报 时间线 不实信息"


async def test_rewrite_query_falls_back() -> None:
    assert await rewrite_search_query(None, "原始查询") == "原始查询"
    bad = StubGateway("")
    assert await rewrite_search_query(bad, "原始查询") == "原始查询"
