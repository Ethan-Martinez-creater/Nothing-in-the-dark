"""Real-PostgreSQL Phase 1 domain acceptance check.

Drives the full expert chain against the real configured PostgreSQL
database: Coordinator dispatches opinion -> propagation -> verification
-> evidence_critic -> citation_validator. Acceptance criteria
(remaining.md 5.1-5.4 + 6):

* the ``claims`` table holds the persisted candidates,
* every claim has at least one ``evidence`` row (its source post),
* ``propagation_edges`` holds observed edges from explicit platform
  relations (and inferred edges from entity overlap),
* every expert persists its own artifact, including ``evidence_review``
  and ``citation_validation``.

The default gateway is scripted (deterministic, no LLM cost). Pass
``--real-llm`` to drive the chain with the configured DeepSeek gateway
(looser assertions since model behaviour is not scripted).

Run from Project\\backend:
    .venv\\Scripts\\python scripts\\smoke_phase1_claims_evidence.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from typing import Any
from uuid import uuid4

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.application.agent_service import AgentRunService  # noqa: E402
from app.application.graph_worker import GraphWorker  # noqa: E402
from app.application.repositories import ApplicationRepository  # noqa: E402
from app.bootstrap import create_checkpointer  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.harness.skills import SkillRegistry  # noqa: E402
from app.harness.tool_factory import build_tool_registry  # noqa: E402
from app.infrastructure.crawler.demo import DemoCrawlerAdapter  # noqa: E402
from app.infrastructure.database import Database  # noqa: E402
from app.infrastructure.database.knowledge_repository import KnowledgeRepository  # noqa: E402
from app.infrastructure.database.social_repository import SocialRepository  # noqa: E402
from app.infrastructure.embeddings import EmbeddingWorkerClient  # noqa: E402
from app.infrastructure.llm import (  # noqa: E402
    LLMGateway,
    LLMMessage,
    LLMResponse,
    ModelRoute,
    OpenAICompatibleGateway,
    ToolCall,
)
from app.schemas.cases import CreateCaseRequest  # noqa: E402


def _response(content: str) -> LLMResponse:
    return LLMResponse(
        message=LLMMessage(role="assistant", content=content),
        model="fake-model",
    )


def _tool_response(name: str, arguments: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        message=LLMMessage(role="assistant"),
        tool_calls=[ToolCall(id="placeholder", name=name, arguments=arguments)],
        model="fake-model",
    )


# Three posts with an explicit reply relation (p2 -> p1) and a shared
# entity (2026年8月1日) so both observed and inferred edges are produced.
_POSTS = [
    {
        "id": "smoke-p1",
        "platform": "weibo",
        "author": "smoke-a",
        "content": "杭州新能源汽车主动召回事件，2026年8月1日发布首份情况通报。",
        "published_at": "2026-08-01T00:00:00+00:00",
        "sentiment": "neutral",
        "engagement": 3200,
        "is_demo": True,
    },
    {
        "id": "smoke-p2",
        "platform": "weibo",
        "author": "smoke-b",
        "content": "2026年8月1日的召回通报引发大量讨论，用户质疑补偿方案。",
        "published_at": "2026-08-01T03:00:00+00:00",
        "reply_to_id": "smoke-p1",
        "sentiment": "negative",
        "engagement": 5100,
        "is_demo": True,
    },
    {
        "id": "smoke-p3",
        "platform": "bilibili",
        "author": "smoke-c",
        "content": "视频：2026年8月1日主动召回事件时间线梳理。",
        "published_at": "2026-08-01T05:00:00+00:00",
        "sentiment": "neutral",
        "engagement": 900,
        "is_demo": True,
    },
]


class ScriptedGateway(LLMGateway):
    """Deterministic six-expert chain with domain tool calls."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._call_id = 0

    @property
    def configured(self) -> bool:
        return True

    async def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        route: ModelRoute,
        temperature: float = 0,
    ) -> LLMResponse:
        system = messages[0].content or ""
        agent = self._detect_agent(system)
        step = self._counters[agent]
        self._counters[agent] += 1
        response = self._script(agent, step)
        if response.tool_calls:
            for call in response.tool_calls:
                self._call_id += 1
                call.id = f"call-{agent}-{self._call_id}-{uuid4().hex[:8]}"
        return response

    def _script(self, agent: str, step: int) -> LLMResponse:
        if agent == "coordinator":
            steps = [
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "opinion",
                        "instructions": (
                            "分析本案例的观点与情感分布，输出结构化 JSON。"
                        ),
                        "input_data": {},
                    },
                ),
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "propagation",
                        "instructions": (
                            "重建传播候选边，输出带特征分数与证据 ID 的图。"
                        ),
                        "input_data": {},
                    },
                ),
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "verification",
                        "instructions": (
                            "抽取可核验主张并做证据核查，输出核查卡。"
                        ),
                        "input_data": {},
                    },
                ),
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "evidence_critic",
                        "instructions": "批判性审查核查卡与证据蕴含关系。",
                        "input_data": {},
                    },
                ),
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "citation_validator",
                        "instructions": "校验报告引用是否真实存在。",
                        "input_data": {},
                    },
                ),
                _response("五名专家均已完成。"),
            ]
            return steps[step] if step < len(steps) else _response('{"done": true}')
        if agent == "opinion":
            steps = [
                _tool_response("analyze_opinion", {"posts": _POSTS}),
                _response(
                    json.dumps(
                        {
                            "conclusions": [
                                {
                                    "claim": "讨论集中于召回信息不确定性",
                                    "evidence_ids": ["smoke-p2"],
                                    "confidence": 0.75,
                                }
                            ],
                            "statistics": {"total_posts": 3},
                            "limitations": ["仅覆盖当前样本"],
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
            return steps[step] if step < len(steps) else _response('{"done": true}')
        if agent == "propagation":
            steps = [
                _tool_response("reconstruct_propagation", {"posts": _POSTS}),
                _response(
                    json.dumps(
                        {
                            "nodes": [
                                {"id": "smoke-p1", "platform": "weibo"},
                                {"id": "smoke-p2", "platform": "weibo"},
                                {"id": "smoke-p3", "platform": "bilibili"},
                            ],
                            "edges": [
                                {
                                    "source": "smoke-p1",
                                    "target": "smoke-p2",
                                    "relation": "observed",
                                    "confidence": 0.85,
                                    "reasons": ["平台显式reply关系"],
                                }
                            ],
                            "origin_candidates": [
                                {"node_id": "smoke-p1", "confidence": 0.5}
                            ],
                            "limitations": [],
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
            return steps[step] if step < len(steps) else _response('{"done": true}')
        if agent == "verification":
            steps = [
                _tool_response(
                    "verify_claims",
                    {"posts": _POSTS, "topic": "主动召回"},
                ),
                _response(
                    json.dumps(
                        {
                            "cards": [
                                {
                                    "claim": "召回范围覆盖杭州地区",
                                    "verdict": "insufficient",
                                    "confidence": 0.5,
                                    "reason": "样本内缺少权威来源",
                                    "supporting_evidence": ["smoke-p1"],
                                    "contradicting_evidence": [],
                                }
                            ],
                            "limitations": ["未使用通用网页搜索"],
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
            return steps[step] if step < len(steps) else _response('{"done": true}')
        if agent == "evidence_critic":
            steps = [
                _tool_response(
                    "query_claims",
                    {"case_id": "injected", "status": None},
                ),
                _tool_response(
                    "query_evidence",
                    {"case_id": "injected"},
                ),
                _response(
                    json.dumps(
                        {
                            "verdicts": [
                                {
                                    "target": "召回范围覆盖杭州地区",
                                    "verdict": "unsupported",
                                    "reason": "引用证据仅提及事件，不构成范围证据",
                                    "evidence_ids": [],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
            return steps[step] if step < len(steps) else _response('{"done": true}')
        if agent == "citation_validator":
            steps = [
                _tool_response(
                    "query_propagation",
                    {"case_id": "injected"},
                ),
                _tool_response(
                    "query_claims",
                    {"case_id": "injected"},
                ),
                _response(
                    json.dumps(
                        {
                            "checks": [
                                {
                                    "citation": "smoke-p1",
                                    "verdict": "valid",
                                    "reason": "帖子存在于当前案例",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
            return steps[step] if step < len(steps) else _response('{"done": true}')
        return _response('{"done": true}')

    @staticmethod
    def _detect_agent(system: str) -> str:
        if "协调" in system:
            return "coordinator"
        if "观点分析专家" in system:
            return "opinion"
        if "传播重建专家" in system:
            return "propagation"
        if "事实核查专家" in system:
            return "verification"
        if "证据批判评审专家" in system:
            return "evidence_critic"
        if "引用校验专家" in system:
            return "citation_validator"
        return "unknown"


class RealGateway(LLMGateway):
    """Thin wrapper so --real-llm mode uses the configured DeepSeek gateway."""

    def __init__(self, settings: Any) -> None:
        self._gateway = OpenAICompatibleGateway(settings)

    @property
    def configured(self) -> bool:
        return self._gateway.configured

    async def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        route: ModelRoute,
        temperature: float = 0,
    ) -> LLMResponse:
        return await self._gateway.complete(
            messages=messages,
            tools=tools,
            route=route,
            temperature=temperature,
        )


async def _run_smoke(real_llm: bool) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    await database.create_schema()
    repository = ApplicationRepository(database)
    knowledge = KnowledgeRepository(database)
    social = SocialRepository(database)
    embeddings = EmbeddingWorkerClient(
        settings.embedding_worker_url,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    skills = SkillRegistry()
    gateway: LLMGateway = RealGateway(settings) if real_llm else ScriptedGateway()
    tools = build_tool_registry(
        DemoCrawlerAdapter(),
        skills,
        knowledge,
        embeddings,
        social,
        repository,
    )
    checkpointer, checkpointer_cm = await create_checkpointer(settings.database_url)
    worker = GraphWorker(
        repository,
        gateway,
        tools,
        skills,
        worker_id="smoke-phase1-worker",
        poll_interval_seconds=0.2,
        lease_seconds=60,
        max_turns=10,
        max_tool_calls=30,
        max_cost=5.0,
        checkpointer=checkpointer,
    )
    service = AgentRunService(repository, worker)

    case = await repository.create_case(
        CreateCaseRequest(
            topic="杭州新能源汽车主动召回事件",
            description="Phase 1 领域算法冒烟验收（claims/evidence/edges）。",
            platforms=["weibo", "bilibili"],
        )
    )
    # Persist the bounded sample first so propagation edges and evidence can
    # reference real source_post ids.
    persisted = await social.persist_batch(case_id=case.id, posts=_POSTS)
    run = await service.start(
        case_id=case.id,
        content=(
            "请对“杭州新能源汽车主动召回事件”完成观点分析、传播重建、事实核查、"
            "证据批判和引用校验。通过 dispatch_expert 依次委派 opinion、"
            "propagation、verification、evidence_critic 和 citation_validator "
            "专家，并汇总它们的 Artifact。"
        ),
        approve_crawl=True,
    )
    await worker.start()
    try:
        deadline = asyncio.get_event_loop().time() + 600
        while True:
            current = await repository.get_agent_run(run.id)
            if current.status in {"completed", "failed", "cancelled"}:
                break
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("smoke run did not finish in 600s")
            await asyncio.sleep(0.5)
    finally:
        await worker.stop()
        if checkpointer_cm is not None:
            await checkpointer_cm.__aexit__(None, None, None)
        await database.dispose()

    children = await repository.list_child_runs(run.id)
    artifacts = []
    for child in children:
        artifacts.extend(await repository.list_run_artifacts(child.id))
    claims = await repository.list_claims_by_case(case.id)
    evidence = await repository.list_evidence_by_case(case.id)
    edges = await repository.list_propagation_edges_by_case(case.id)

    summary: dict[str, Any] = {
        "case_id": case.id,
        "persisted_posts": {
            "posts_created": persisted.posts_created,
            "raw_records_created": persisted.raw_records_created,
        },
        "parent_run": {
            "id": run.id,
            "status": current.status,
            "estimated_cost": current.estimated_cost,
        },
        "children": [
            {
                "id": child.id,
                "agent": child.agent,
                "status": child.status,
            }
            for child in children
        ],
        "artifacts": [
            {
                "artifact_id": artifact.id,
                "kind": artifact.kind,
                "version": artifact.version,
            }
            for artifact in artifacts
        ],
        "domain_tables": {
            "claims": [
                {"claim_id": claim.id, "status": claim.status}
                for claim in claims
            ],
            "evidence_count": len(evidence),
            "evidence_linked_to_claim": sum(
                1 for item in evidence if item.claim_id is not None
            ),
            "propagation_edges": [
                {
                    "relation": edge.relation,
                    "source_post_id": edge.source_post_id,
                    "target_post_id": edge.target_post_id,
                    "confidence": edge.confidence,
                    "algorithm_version": edge.algorithm_version,
                }
                for edge in edges
            ],
        },
        "real_llm": real_llm,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # Acceptance assertions (remaining.md 5.1-5.4 + 6).
    assert current.status == "completed", "parent run must complete"
    assert len(claims) >= 1, "claims table must hold persisted claims"
    assert len(evidence) >= 1, "evidence table must hold rows"
    assert any(
        item.claim_id is not None for item in evidence
    ), "evidence must link back to claims"
    if not real_llm:
        assert {child.agent for child in children} == {
            "opinion",
            "propagation",
            "verification",
            "evidence_critic",
            "citation_validator",
        }, "all five experts must be dispatched"
        assert all(child.status == "completed" for child in children)
        kinds = {artifact.kind for artifact in artifacts}
        assert "evidence_review" in kinds, "evidence_critic must persist its artifact"
        assert (
            "citation_validation" in kinds
        ), "citation_validator must persist its artifact"
        observed = [edge for edge in edges if edge.relation == "observed"]
        assert len(observed) >= 1, (
            "observed edges must be persisted from explicit relations"
        )
        assert all(
            edge.algorithm_version == "1.0.0" for edge in edges
        ), "every edge must carry the algorithm version"
    print("SMOKE OK: claims / evidence / propagation edges verified on PostgreSQL")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--real-llm",
        action="store_true",
        help="Drive the chain with the configured DeepSeek gateway instead of the script.",
    )
    args = parser.parse_args()
    asyncio.run(_run_smoke(args.real_llm))


if __name__ == "__main__":
    main()
