"""Real-PostgreSQL multi-agent acceptance check.

Drives a full expert chain against the real configured PostgreSQL database:
Coordinator dynamically dispatches opinion -> verification -> evidence_critic
-> report expert runs, each calling its own tools, and each persists a
structured artifact bound to its run; the parent receives typed mailbox
messages. This is the acceptance criterion of remaining.md 3.2.

The default gateway is scripted (deterministic, no LLM cost). Pass
``--real-llm`` to drive the chain with the configured DeepSeek gateway
(looser assertions since model behaviour is not scripted).

Run from Project\\backend:
    .venv\\Scripts\\python scripts\\smoke_expert_agents.py
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


_POSTS = [
    {
        "id": "smoke-p1",
        "platform": "weibo",
        "author": "smoke-a",
        "content": "杭州新能源汽车主动召回事件最新进展",
        "published_at": "2026-08-01T00:00:00+00:00",
        "sentiment": "negative",
        "engagement": 3200,
        "is_demo": True,
    },
    {
        "id": "smoke-p2",
        "platform": "bilibili",
        "author": "smoke-b",
        "content": "主动召回时间线整理视频",
        "published_at": "2026-08-01T03:00:00+00:00",
        "sentiment": "neutral",
        "engagement": 5100,
        "is_demo": True,
    },
]


class ScriptedGateway(LLMGateway):
    """Deterministic multi-agent chain: coordinator dispatches four experts."""

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
                # Unique across process restarts so tool_calls ids never
                # collide with records left by a previous smoke run.
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
                        "agent": "verification",
                        "instructions": (
                            "对案例中的可核验主张做证据核查，输出核查卡。"
                        ),
                        "input_data": {},
                    },
                ),
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "evidence_critic",
                        "instructions": "批判性审查已产出的核查卡与证据蕴含关系。",
                        "input_data": {},
                    },
                ),
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "report",
                        "instructions": "汇总已有 Artifact 生成最终报告。",
                        "input_data": {},
                    },
                ),
                _response("四名专家均已完成，报告见 Artifact。"),
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
                                    "claim": "负面情绪集中在召回原因信息不确定性",
                                    "evidence_ids": ["smoke-p1"],
                                    "confidence": 0.75,
                                }
                            ],
                            "statistics": {"total_posts": 2},
                            "limitations": ["仅覆盖当前样本"],
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
            return steps[step] if step < len(steps) else _response('{"done": true}')
        if agent == "verification":
            steps = [
                _tool_response("verify_claims", {"posts": _POSTS, "topic": "主动召回"}),
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
            return _response(
                json.dumps(
                    {
                        "verdicts": [
                            {
                                "target": "召回范围覆盖杭州地区",
                                "verdict": "unsupported",
                                "reason": "引用证据仅提及事件，不构成范围证据",
                                "evidence_ids": ["smoke-p1"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        if agent == "report":
            steps = [
                _tool_response(
                    "get_artifact",
                    {"kind": "opinion_analysis"},
                ),
                _tool_response(
                    "get_artifact",
                    {"kind": "fact_check"},
                ),
                _response(
                    json.dumps(
                        {
                            "title": "杭州新能源汽车主动召回舆情简报",
                            "executive_summary": "样本显示讨论集中于召回信息不确定性。",
                            "sections": [
                                {"title": "舆论概览", "content": "见 opinion Artifact"}
                            ],
                            "citation_links": [
                                {"claim": "负面情绪集中", "evidence_id": "smoke-p1"}
                            ],
                            "disclaimer": "仅覆盖当前采集样本。",
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
        if "事实核查专家" in system:
            return "verification"
        if "证据批判评审专家" in system:
            return "evidence_critic"
        if "报告生成专家" in system:
            return "report"
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
        worker_id="smoke-expert-worker",
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
            description="多智能体专家链路冒烟验收。",
            platforms=["weibo", "bilibili"],
        )
    )
    run = await service.start(
        case_id=case.id,
        content=(
            "请对“杭州新能源汽车主动召回事件”完成观点分析、事实核查、"
            "证据批判和报告生成。通过 dispatch_expert 依次委派 opinion、"
            "verification、evidence_critic 和 report 专家，并汇总它们的 Artifact。"
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
    mailbox = []
    for child in children:
        child_artifacts = await repository.list_run_artifacts(child.id)
        artifacts.extend(child_artifacts)
        for message in await repository.list_agent_messages(child.id):
            mailbox.append(message)
    turns = await repository.list_turns(case.id)

    summary: dict[str, Any] = {
        "case_id": case.id,
        "parent_run": {
            "id": run.id,
            "status": current.status,
            "agent": current.agent,
            "input_tokens": current.input_tokens,
            "output_tokens": current.output_tokens,
            "estimated_cost": current.estimated_cost,
        },
        "children": [
            {
                "id": child.id,
                "agent": child.agent,
                "status": child.status,
                "input_tokens": child.input_tokens,
                "output_tokens": child.output_tokens,
                "estimated_cost": child.estimated_cost,
            }
            for child in children
        ],
        "artifacts": [
            {
                "artifact_id": artifact.id,
                "kind": artifact.kind,
                "version": artifact.version,
                "run_id": artifact.run_id,
            }
            for artifact in artifacts
        ],
        "mailbox": [
            {
                "message_type": message.message_type,
                "sender_run_id": message.sender_run_id,
                "receiver_run_id": message.receiver_run_id,
                "payload": message.payload,
            }
            for message in mailbox
        ],
        "turn_roles": [turn.role for turn in turns],
        "real_llm": real_llm,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # Acceptance assertions (remaining.md 3.2).
    assert current.status == "completed", "parent run must complete"
    if real_llm:
        # The model may or may not dispatch experts; only require the parent
        # to finish when it did dispatch at least one expert.
        assert len(children) >= 1, "real LLM run should dispatch at least one expert"
    else:
        assert {child.agent for child in children} == {
            "opinion",
            "verification",
            "evidence_critic",
            "report",
        }, "all four experts must be dispatched"
        assert all(child.status == "completed" for child in children)
        assert {artifact.kind for artifact in artifacts} == {
            "opinion_analysis",
            "fact_check",
            "evidence_review",
            "report",
        }, "every expert persists its own artifact"
        assert all(artifact.run_id is not None for artifact in artifacts)
        assert any(
            message.message_type == "expert_completed"
            and message.receiver_run_id == run.id
            for message in mailbox
        ), "parent must receive expert_completed mailbox messages"
        assert [turn.role for turn in turns if turn.role == "user"] == ["user"], (
            "expert runs must not add conversation turns"
        )
    print("SMOKE OK: multi-agent expert chain verified on real PostgreSQL")


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
