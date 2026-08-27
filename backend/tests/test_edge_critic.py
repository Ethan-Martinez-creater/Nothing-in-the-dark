"""P0-1.1d: LLM Edge Critic reviews ambiguous inferred edges."""

from __future__ import annotations

import json

from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse
from app.services.propagation_algorithm import (
    EdgeCandidate,
    criticize_edges,
    criticize_edges_with_llm,
)


class ScriptedCriticGateway(LLMGateway):
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    @property
    def configured(self) -> bool:
        return True

    async def complete(self, *, messages: list[LLMMessage], tools=None, route=None, **kw):
        self.calls += 1
        return LLMResponse(
            message=LLMMessage(role="assistant", content=json.dumps(self.payload)),
            model="fake",
        )


def _posts() -> list[dict[str, object]]:
    return [
        {
            "id": "a",
            "content": "原文称事故发生在港口",
            "published_at": "2026-08-01T08:00:00+00:00",
        },
        {
            "id": "b",
            "content": "转发称事故发生在港口",
            "published_at": "2026-08-01T10:00:00+00:00",
        },
    ]


def test_rule_critic_still_rejects_clock_skew() -> None:
    posts = [
        {"id": "a", "content": "x", "published_at": "2026-08-01T10:00:00+00:00"},
        {"id": "b", "content": "x", "published_at": "2026-08-01T08:00:00+00:00"},
    ]
    edges = [
        EdgeCandidate(
            source_post_id="a",
            target_post_id="b",
            relation="observed",
            confidence=0.9,
            feature_scores={"explicit_relation": 1.0},
            reasons=["reply"],
            evidence_ids=["a", "b"],
        )
    ]
    critique = criticize_edges(posts, edges)
    assert critique["rejected"]


async def test_llm_critic_skips_when_gateway_missing() -> None:
    critique = {"kept": [], "rejected": [], "notes": []}
    result = await criticize_edges_with_llm(None, _posts(), critique)
    assert result["llm_review"]["available"] is False


async def test_llm_critic_rejects_ambiguous_inferred_edge() -> None:
    edges = [
        EdgeCandidate(
            source_post_id="a",
            target_post_id="b",
            relation="inferred",
            confidence=0.45,
            feature_scores={
                "time_decay": 0.5,
                "text_similarity": 0.7,
                "entity_overlap": 0.2,
            },
            reasons=["文本相似度"],
            evidence_ids=["a", "b"],
        )
    ]
    critique = criticize_edges(_posts(), edges)
    gateway = ScriptedCriticGateway(
        {
            "reviews": [
                {
                    "source": "a",
                    "target": "b",
                    "verdict": "reject",
                    "reason": "相似度来自套话，不足以构成传播",
                }
            ]
        }
    )
    result = await criticize_edges_with_llm(gateway, _posts(), critique)
    assert gateway.calls == 1
    assert result["llm_review"]["available"] is True
    rejected_ids = {item["id"] for item in result["rejected"]}
    assert "a->b" in rejected_ids
    kept_ids = {item["id"] for item in result["kept"]}
    assert "a->b" not in kept_ids
