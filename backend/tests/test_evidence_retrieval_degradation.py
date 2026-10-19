"""Embedding outages should retain case-scoped keyword evidence retrieval."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.core.errors import ApplicationError
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


class FakeEmbeddings:
    def __init__(self, result: list[list[float]] | Exception) -> None:
        self.result = result
        self.queries: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.queries.append(texts)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeKnowledge:
    def __init__(self, *, hit_query: str) -> None:
        self.hit_query = hit_query
        self.searches: list[dict[str, Any]] = []

    async def search(
        self,
        *,
        case_id: str,
        query: str,
        limit: int,
        embedding: list[float] | None = None,
        source_types: set[str] | None = None,
        platforms: list[str] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
    ) -> list[RagHit]:
        self.searches.append(
            {
                "case_id": case_id,
                "query": query,
                "limit": limit,
                "embedding": embedding,
                "platforms": platforms,
                "time_from": time_from,
                "time_to": time_to,
            }
        )
        if query != self.hit_query:
            return []
        return [
            RagHit(
                evidence_id="social_post:post-1",
                source_type="social_post",
                source_id="post-1",
                content="调查相关帖子内容",
                score=0.75,
                retrieval_modes=["keyword" if embedding is None else "vector"],
                platform="weibo",
                source_url="https://weibo.com/post-1",
            )
        ]


async def test_embedding_outage_falls_back_once_and_preserves_search_scope() -> None:
    original = "杭州事件"
    rewritten = "杭州 事件 通报"
    embeddings = FakeEmbeddings(
        ApplicationError("embedding service unavailable", code="embedding_worker_unavailable")
    )
    knowledge = FakeKnowledge(hit_query=original)
    registry = build_tool_registry(
        DemoCrawlerAdapter(),
        knowledge=knowledge,
        embeddings=embeddings,
        llm=StubGateway(rewritten),
    )

    output = await registry.invoke(
        "search_social_evidence",
        {
            "case_id": "case-17",
            "query": original,
            "limit": 7,
            "platforms": ["weibo"],
            "time_range": {
                "from": "2025-03-01T00:00:00",
                "to": "2025-03-31T23:59:59",
            },
        },
    )

    assert output["available"] is True
    assert output["retrieval_degraded"] is True
    assert output["degradation_reason"] == "embedding_worker_unavailable"
    assert output["retrieval_mode"] == "keyword_only"
    assert [call["query"] for call in knowledge.searches] == [rewritten, original]
    assert embeddings.queries == [[rewritten]]
    assert all(call["embedding"] is None for call in knowledge.searches)
    assert all(call["case_id"] == "case-17" for call in knowledge.searches)
    assert all(call["platforms"] == ["weibo"] for call in knowledge.searches)
    assert [call["time_from"] for call in knowledge.searches] == [
        datetime(2025, 3, 1),
        datetime(2025, 3, 1),
    ]
    assert [call["time_to"] for call in knowledge.searches] == [
        datetime(2025, 3, 31, 23, 59, 59),
        datetime(2025, 3, 31, 23, 59, 59),
    ]
    assert output["hits"][0]["evidence_id"] == "social_post:post-1"


async def test_embedding_success_keeps_hybrid_mode() -> None:
    query = "杭州事件"
    embeddings = FakeEmbeddings([[0.1, 0.2]])
    knowledge = FakeKnowledge(hit_query=query)
    registry = build_tool_registry(
        DemoCrawlerAdapter(),
        knowledge=knowledge,
        embeddings=embeddings,
        llm=None,
    )

    output = await registry.invoke(
        "search_social_evidence",
        {"case_id": "case-18", "query": query, "platforms": ["weibo"]},
    )

    assert output["retrieval_degraded"] is False
    assert output["degradation_reason"] is None
    assert output["retrieval_mode"] == "hybrid"
    assert embeddings.queries == [[query]]
    assert knowledge.searches[0]["embedding"] == [0.1, 0.2]
    assert knowledge.searches[0]["case_id"] == "case-18"
    assert knowledge.searches[0]["platforms"] == ["weibo"]


async def test_unrelated_embedding_error_is_not_swallowed() -> None:
    failure = ApplicationError("provider request failed", code="embedding_provider_error")
    embeddings = FakeEmbeddings(failure)
    knowledge = FakeKnowledge(hit_query="杭州事件")
    registry = build_tool_registry(
        DemoCrawlerAdapter(),
        knowledge=knowledge,
        embeddings=embeddings,
        llm=None,
    )

    with pytest.raises(ApplicationError) as exc_info:
        await registry.invoke(
            "search_social_evidence",
            {"case_id": "case-19", "query": "杭州事件"},
        )

    assert exc_info.value is failure
    assert knowledge.searches == []
