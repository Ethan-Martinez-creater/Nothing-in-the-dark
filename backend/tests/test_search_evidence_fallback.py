"""Regression: search_social_evidence must fall back to the original query.

LLM query rewrite expands a short Chinese query into many space-separated
terms; the Postgres keyword branch matches them with ``ILIKE ALL`` (AND
semantics), so the expanded query almost always returns zero hits even when
matching posts exist. ``search_evidence`` must retry with the original query
when the rewritten query comes back empty.
"""

from __future__ import annotations

from typing import Any

from app.harness.tool_factory import build_tool_registry
from app.infrastructure.crawler.demo import DemoCrawlerAdapter
from app.infrastructure.database.knowledge_repository import RagHit
from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse


class StubGateway(LLMGateway):
    def __init__(self, content: str) -> None:
        self._content = content

    @property
    def configured(self) -> bool:
        return True

    async def complete(self, *, messages: list[LLMMessage], route=None, **kw):
        return LLMResponse(
            message=LLMMessage(role="assistant", content=self._content),
            model="fake",
        )


class FakeKnowledge:
    """search() returns hits only for the original query."""

    def __init__(self, *, original: str, expanded: str) -> None:
        self._original = original
        self._expanded = expanded
        self.searched: list[str] = []

    async def search(
        self,
        *,
        case_id: str,
        query: str,
        limit: int,
        embedding: list[float] | None = None,
        source_types: set[str] | None = None,
        platforms: list[str] | None = None,
        time_from: Any = None,
        time_to: Any = None,
    ) -> list[RagHit]:
        self.searched.append(query)
        if query != self._original:
            return []
        return [
            RagHit(
                evidence_id="social_post:1",
                source_type="social_post",
                source_id="1",
                content="杭州 电梯 诬告 女子 反转 通报",
                score=1,
                retrieval_modes=["keyword"],
                platform="weibo",
                source_url="https://weibo.com/1",
            )
        ]


async def test_falls_back_to_original_query_when_rewrite_empty() -> None:
    expanded = "杭州 杭州市 杭城 浙江杭州 杭州事件"
    crawler = DemoCrawlerAdapter()
    knowledge = FakeKnowledge(original="杭州", expanded=expanded)
    registry = build_tool_registry(
        crawler,
        knowledge=knowledge,
        llm=StubGateway(expanded),
    )

    output = await registry.invoke(
        "search_social_evidence",
        {"case_id": "c1", "query": "杭州", "limit": 10},
    )

    assert output["available"] is True
    assert len(output["hits"]) == 1
    assert output["hits"][0]["evidence_id"] == "social_post:1"
    # 先尝试扩写词，空命中后降级原始词。
    assert knowledge.searched == [expanded, "杭州"]


async def test_no_extra_search_when_rewrite_already_hits() -> None:
    crawler = DemoCrawlerAdapter()
    knowledge = FakeKnowledge(original="杭州", expanded="杭州")
    # rewrite 返回与原始词相同（LLM 未扩写），一次命中即可，不重复检索。
    registry = build_tool_registry(
        crawler,
        knowledge=knowledge,
        llm=StubGateway("杭州"),
    )

    output = await registry.invoke(
        "search_social_evidence",
        {"case_id": "c1", "query": "杭州", "limit": 10},
    )

    assert len(output["hits"]) == 1
    assert knowledge.searched == ["杭州"]


async def test_empty_result_still_returns_empty_without_llm() -> None:
    # knowledge 为空（未装配）时保持原有空结果语义，不抛错。
    crawler = DemoCrawlerAdapter()
    registry = build_tool_registry(crawler, knowledge=None, llm=None)

    output = await registry.invoke(
        "search_social_evidence",
        {"case_id": "c1", "query": "杭州", "limit": 10},
    )

    assert output["available"] is False
    assert output["hits"] == []


async def test_missing_query_falls_back_to_case_topic() -> None:
    # LLM 漏生成 query：用 case topic 兜底继续检索，而不是校验失败中断整轮。
    class FakeRepository:
        async def get_case(self, case_id: str):
            assert case_id == "c1"
            return type("Case", (), {"topic": "杭州 电梯 诬告"})()

    crawler = DemoCrawlerAdapter()
    knowledge = FakeKnowledge(original="杭州 电梯 诬告", expanded="杭州 电梯 诬告")
    registry = build_tool_registry(
        crawler,
        knowledge=knowledge,
        llm=None,
        repository=FakeRepository(),
    )

    output = await registry.invoke(
        "search_social_evidence",
        {"case_id": "c1", "limit": 10},
    )

    assert output["available"] is True
    assert len(output["hits"]) == 1
    # 兜底 query 应走 knowledge.search。
    assert knowledge.searched == ["杭州 电梯 诬告"]


async def test_missing_query_without_repository_returns_empty_not_error() -> None:
    # repository 未装配时，query 缺失也不应抛错（保持空结果语义）。
    crawler = DemoCrawlerAdapter()
    registry = build_tool_registry(crawler, knowledge=None, llm=None)

    output = await registry.invoke(
        "search_social_evidence",
        {"case_id": "c1", "limit": 10},
    )

    assert output["available"] is False
    assert output["hits"] == []
