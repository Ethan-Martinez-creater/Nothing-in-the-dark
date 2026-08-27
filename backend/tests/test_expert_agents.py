"""Multi-agent expert runtime: dynamic delegation, artifacts, mailbox."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver

from app.application.agent_service import AgentRunService
from app.application.graph_worker import GraphWorker
from app.application.repositories import ApplicationRepository
from app.harness.agents import ExpertKind, build_definition_for
from app.harness.skills import SkillRegistry
from app.harness.tool_factory import build_tool_registry
from app.infrastructure.crawler.demo import DemoCrawlerAdapter
from app.infrastructure.database import Database
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.embeddings import EmbeddingWorkerClient
from app.infrastructure.llm import (
    LLMGateway,
    LLMMessage,
    LLMResponse,
    ModelRoute,
    ToolCall,
)
from app.schemas.cases import CreateCaseRequest


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


def _tool_batch_response(
    calls: list[tuple[str, dict[str, Any]]],
) -> LLMResponse:
    return LLMResponse(
        message=LLMMessage(role="assistant"),
        tool_calls=[
            ToolCall(id="placeholder", name=name, arguments=arguments)
            for name, arguments in calls
        ],
        model="fake-model",
    )


class ScriptedGateway(LLMGateway):
    """Routes scripted responses by the agent role in the system message."""

    def __init__(self, scripts: dict[str, list[LLMResponse]]) -> None:
        self._scripts = scripts
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
        script = self._scripts.get(agent, [])
        response = script[step] if step < len(script) else _response('{"done": true}')
        if response.tool_calls:
            for call in response.tool_calls:
                self._call_id += 1
                call.id = f"call-{agent}-{self._call_id}-{uuid4().hex[:8]}"
        return response

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


async def _build_worker(
    tmp_path: Path,
    gateway: LLMGateway,
    *,
    social: SocialRepository | None = None,
    database: Database | None = None,
) -> tuple[GraphWorker, ApplicationRepository, Database]:
    database = database or Database(f"sqlite+aiosqlite:///{tmp_path / 'expert.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    knowledge = KnowledgeRepository(database)
    social = social or SocialRepository(database)
    embeddings = EmbeddingWorkerClient(
        "http://localhost:1",
        dimensions=1024,
        timeout_seconds=1,
    )
    skills = SkillRegistry()
    tools = build_tool_registry(
        DemoCrawlerAdapter(),
        skills,
        knowledge,
        embeddings,
        social,
        repository,
    )
    worker = GraphWorker(
        repository,
        gateway,
        tools,
        skills,
        worker_id="test-worker",
        poll_interval_seconds=0.05,
        lease_seconds=30,
        max_turns=8,
        max_tool_calls=24,
        max_cost=5.0,
        checkpointer=MemorySaver(),
        social=social,
    )
    return worker, repository, database


async def _wait_for_run(
    repository: ApplicationRepository,
    run_id: str,
    *,
    timeout_seconds: float = 30,
) -> Any:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        run = await repository.get_agent_run(run_id)
        if run.status in {"completed", "failed", "cancelled"}:
            return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish; status={run.status}")


_POSTS = [
    {
        "id": "p1",
        "platform": "weibo",
        "author": "a",
        "content": "事件最新进展",
        "published_at": "2026-01-01T00:00:00+00:00",
        "sentiment": "negative",
        "engagement": 100,
        "is_demo": True,
    },
    {
        "id": "p2",
        "platform": "bilibili",
        "author": "b",
        "content": "分析视频",
        "published_at": "2026-01-01T01:00:00+00:00",
        "sentiment": "neutral",
        "engagement": 200,
        "is_demo": True,
    },
]


async def test_coordinator_dispatches_expert_artifact_and_mailbox(
    tmp_path: Path,
) -> None:
    gateway = ScriptedGateway(
        {
            "coordinator": [
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "opinion",
                        "instructions": "请分析该案例的观点分布，并输出结构化 JSON。",
                        "input_data": {},
                    },
                ),
                _response("已完成观点分析，详见 Artifact。"),
            ],
            "opinion": [
                _tool_response("analyze_opinion", {"posts": _POSTS}),
                _response(
                    json.dumps(
                        {
                            "conclusions": [
                                {
                                    "claim": "负面情绪占主导",
                                    "evidence_ids": ["p1"],
                                    "confidence": 0.8,
                                }
                            ],
                            "statistics": {"total_posts": 2},
                        },
                        ensure_ascii=False,
                    )
                ),
            ],
        }
    )
    worker, repository, database = await _build_worker(tmp_path, gateway)
    service = AgentRunService(repository, worker)
    case = await repository.create_case(
        CreateCaseRequest(topic="测试舆情", platforms=["weibo", "bilibili"])
    )
    run = await service.start(
        case_id=case.id,
        content="分析这个案例",
        approve_crawl=True,
    )
    await worker.start()
    try:
        finished = await _wait_for_run(repository, run.id)
    finally:
        await worker.stop()
        await database.dispose()
    assert finished.status == "completed"

    children = await repository.list_child_runs(run.id)
    assert len(children) == 1
    child = children[0]
    assert child.agent == "opinion"
    assert child.status == "completed"
    assert child.parent_run_id == run.id

    artifacts = await repository.list_run_artifacts(child.id)
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.kind == "opinion_analysis"
    assert artifact.run_id == child.id
    assert artifact.data["conclusions"][0]["evidence_ids"] == ["p1"]

    messages = await repository.list_agent_messages(run.id)
    completions = [m for m in messages if m.message_type == "expert_completed"]
    assert len(completions) == 1
    assert completions[0].sender_run_id == child.id
    assert completions[0].receiver_run_id == run.id
    assert completions[0].payload["artifact_id"] == artifact.id

    # Expert runs contribute their final answer as an assistant turn (so the
    # bubble shows inline output) instead of only persisting an artifact.
    turns = await repository.list_turns(case.id)
    assert [turn.role for turn in turns] == ["user", "assistant", "assistant"]
    # The expert run is linked to its own answer turn so the frontend merges
    # that turn's content as the run's final answer.
    linked = await repository.get_agent_run(child.id)
    assert linked.turn_id is not None
    answer_turn = next(
        (turn for turn in turns if turn.id == linked.turn_id), None
    )
    assert answer_turn is not None
    assert answer_turn.role == "assistant"
    assert answer_turn.content.strip()


async def test_coordinator_dispatches_two_experts_in_parallel(
    tmp_path: Path,
) -> None:
    gateway = ScriptedGateway(
        {
            "coordinator": [
                _tool_batch_response(
                    [
                        (
                            "dispatch_expert",
                            {
                                "agent": "opinion",
                                "instructions": "分析观点分布。",
                                "input_data": {},
                            },
                        ),
                        (
                            "dispatch_expert",
                            {
                                "agent": "propagation",
                                "instructions": "重建传播路径。",
                                "input_data": {},
                            },
                        ),
                    ]
                ),
                _response("两个专家均已完成。"),
            ],
            "opinion": [
                _tool_response("analyze_opinion", {"posts": _POSTS}),
                _response('{"conclusions": [], "statistics": {"total_posts": 2}}'),
            ],
            "propagation": [
                _tool_response("reconstruct_propagation", {"posts": _POSTS}),
                _response(
                    '{"nodes": [], "edges": [], "origin_candidates": [], '
                    '"limitations": []}'
                ),
            ],
        }
    )
    worker, repository, database = await _build_worker(tmp_path, gateway)
    service = AgentRunService(repository, worker)
    case = await repository.create_case(
        CreateCaseRequest(topic="并行测试", platforms=["weibo"])
    )
    run = await service.start(
        case_id=case.id,
        content="并行分析",
        approve_crawl=True,
    )
    await worker.start()
    try:
        finished = await _wait_for_run(repository, run.id)
    finally:
        await worker.stop()
        await database.dispose()
    assert finished.status == "completed"

    children = await repository.list_child_runs(run.id)
    assert {child.agent for child in children} == {"opinion", "propagation"}
    assert all(child.status == "completed" for child in children)

    kinds = set()
    for child in children:
        artifacts = await repository.list_run_artifacts(child.id)
        assert len(artifacts) == 1
        kinds.add(artifacts[0].kind)
    assert kinds == {"opinion_analysis", "propagation_reconstruction"}


async def test_dispatch_key_idempotency(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'idem.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="幂等测试", platforms=["weibo"])
    )
    turn = await repository.add_turn(case.id, role="user", content="测试")
    parent = await repository.create_agent_run(
        case_id=case.id,
        turn_id=turn.id,
        objective="父任务",
    )
    child = await repository.create_agent_run(
        case_id=case.id,
        turn_id=None,
        objective="子任务",
        agent="opinion",
        parent_run_id=parent.id,
        metadata={"dispatch": {"dispatch_key": "k1"}},
    )
    found = await repository.get_child_run_by_dispatch_key(parent.id, "k1")
    assert found is not None and found.id == child.id
    assert await repository.get_child_run_by_dispatch_key(parent.id, "k2") is None


async def test_worker_routes_definitions_by_agent(tmp_path: Path) -> None:
    gateway = ScriptedGateway({})
    worker, repository, database = await _build_worker(tmp_path, gateway)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="路由测试", platforms=["weibo"])
        )
        turn = await repository.add_turn(case.id, role="user", content="测试")
        parent = await repository.create_agent_run(
            case_id=case.id,
            turn_id=turn.id,
            objective="父任务",
        )
        child = await repository.create_agent_run(
            case_id=case.id,
            turn_id=None,
            objective="子任务",
            agent="verification",
            parent_run_id=parent.id,
        )
        verification = worker._definition_for(child)
        assert verification.name == "verification"
        assert verification.model_route == ModelRoute.REASONING
        assert "verify_claims" in verification.allowed_tools
        assert "dispatch_expert" not in verification.allowed_tools

        coordinator = worker._definition_for(parent)
        assert coordinator.name == "coordinator"
        assert "dispatch_expert" in coordinator.allowed_tools
        assert "get_artifact" in coordinator.allowed_tools
        assert "analyze_opinion" not in coordinator.allowed_tools
    finally:
        await database.dispose()


def test_build_definition_for_every_kind() -> None:
    for kind in ExpertKind:
        definition = build_definition_for(kind)
        assert definition.name == kind.value
        assert definition.instructions
        assert definition.allowed_tools
        assert definition.permissions

    opinion = build_definition_for(ExpertKind.OPINION)
    assert "analyze_opinion" in opinion.allowed_tools
    assert opinion.model_route == ModelRoute.FAST

    critic = build_definition_for(ExpertKind.EVIDENCE_CRITIC)
    assert critic.model_route == ModelRoute.REASONING
    assert "verify_claims" not in critic.allowed_tools
    assert "query_claims" in critic.allowed_tools
    assert "query_evidence" in critic.allowed_tools

    validator = build_definition_for(ExpertKind.CITATION_VALIDATOR)
    assert "analyze_opinion" not in validator.allowed_tools
    assert "get_artifact" in validator.allowed_tools
    assert "query_propagation" in validator.allowed_tools
    assert "read_skill" in validator.permissions

    opinion = build_definition_for(ExpertKind.OPINION)
    assert "classify_sentiment" in opinion.allowed_tools

    propagation = build_definition_for(ExpertKind.PROPAGATION)
    assert "query_propagation" in propagation.allowed_tools

    verification = build_definition_for(ExpertKind.VERIFICATION)
    assert "query_claims" in verification.allowed_tools
    assert "query_evidence" in verification.allowed_tools


async def test_domain_repository_crud_and_idempotency(tmp_path: Path) -> None:
    """Claim / Evidence / PropagationEdge CRUD round-trip on SQLite."""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'domain.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="领域CRUD", platforms=["weibo"])
        )
        run = await repository.create_agent_run(
            case_id=case.id,
            turn_id=None,
            objective="主张抽取",
        )

        claim = await repository.create_claim(
            case_id=case.id,
            text="官方回应称事故无伤亡",
            created_by_run_id=run.id,
        )
        assert claim.status == "open"
        updated = await repository.update_claim_verdict(
            claim.id,
            verdict="insufficient",
            status="closed",
            confidence=0.5,
        )
        assert updated.verdict == "insufficient"
        assert (await repository.get_claim(claim.id)).status == "closed"

        evidence = await repository.create_evidence(
            case_id=case.id,
            claim_id=claim.id,
            source_type="post",
            source_id="p1",
            stance="support",
            excerpt="现场通报",
            relevance=0.9,
        )
        duplicate = await repository.create_evidence(
            case_id=case.id,
            claim_id=claim.id,
            source_type="post",
            source_id="p1",
            stance="support",
            excerpt="现场通报",
        )
        assert duplicate.id == evidence.id  # idempotent on (case, source, claim)
        assert len(await repository.list_evidence_by_claim(claim.id)) == 1
        assert len(await repository.list_evidence_by_case(case.id)) == 1

        # Propagation edges are idempotent on (case, source, target); the
        # FK targets are opaque here because SQLite does not enforce them.
        edge = await repository.create_propagation_edge(
            case_id=case.id,
            source_post_id="s1",
            target_post_id="t1",
            relation="observed",
            confidence=0.9,
            feature_scores={"explicit_relation": 1.0},
            evidence_ids=["e1"],
        )
        again = await repository.create_propagation_edge(
            case_id=case.id,
            source_post_id="s1",
            target_post_id="t1",
            relation="observed",
            confidence=0.9,
            feature_scores={},
            evidence_ids=[],
        )
        assert again.id == edge.id
        edges = await repository.list_propagation_edges_by_case(
            case.id,
            relation="observed",
            min_confidence=0.8,
        )
        assert len(edges) == 1
        assert edges[0].algorithm_version == "1.0.0"
    finally:
        await database.dispose()


async def test_propagation_expert_artifact_backfills_edge_ids(
    tmp_path: Path,
) -> None:
    """传播专家在子 run 里直接产出图并落 artifact，必须同 coordinator 的
    propagation 工具一样持久化边并回填 edge_id（2026-08-08 冒烟发现：
    专家路径此前绕过 _persist_propagation_edges，前端边确认按钮无 id
    可用）。"""
    gateway = ScriptedGateway(
        {
            "coordinator": [
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "propagation",
                        "instructions": "请构建传播链并输出结构化 JSON。",
                        "input_data": {},
                    },
                ),
                _response("已完成传播重构，详见 Artifact。"),
            ],
            "propagation": [
                _response(
                    json.dumps(
                        {
                            "stages": [
                                {"name": "首发", "window": "00:00-01:00"},
                            ],
                            "edges": [
                                {
                                    "source": "p1",
                                    "target": "p2",
                                    "relation": "inferred",
                                    "confidence": 0.6,
                                    "feature_scores": {"decay": 0.6},
                                    "evidence_ids": ["p1"],
                                    "algorithm_version": "1.0.0",
                                }
                            ],
                            "source_candidates": ["p1"],
                        }
                    )
                ),
            ],
        }
    )
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'expert_edge.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="专家边回填", platforms=["weibo", "bilibili"])
        )
        await social.persist_batch(case_id=case.id, posts=_POSTS)
        worker, repository, _ = await _build_worker(
            tmp_path,
            gateway,
            social=social,
            database=database,
        )
        service = AgentRunService(repository, worker)
        run = await service.start(
            case_id=case.id,
            content="分析传播链路",
            approve_crawl=True,
        )
        await worker.start()
        try:
            await _wait_for_run(repository, run.id)
        finally:
            await worker.stop()

        edges = await repository.list_propagation_edges_by_case(case.id)
        assert len(edges) == 1
        assert edges[0].relation == "inferred"

        artifacts = await repository.list_artifacts(case.id)
        propagation = [
            a for a in artifacts if a.kind == "propagation_reconstruction"
        ]
        assert len(propagation) == 1
        data = propagation[0].data
        stored = {edge["source"]: edge for edge in data["edges"]}
        assert stored["p1"]["edge_id"] == edges[0].id
    finally:
        await database.dispose()


async def test_verification_expert_persists_claims_and_evidence(
    tmp_path: Path,
) -> None:
    """核查专家绕过 verify_claims 工具时，核查卡必须持久化到
    claims/evidence 表（否则证据侧栏显示 0，2026-08-10 反馈）。"""
    gateway = ScriptedGateway(
        {
            "coordinator": [
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "verification",
                        "instructions": "请核查主张并输出结构化 JSON。",
                        "input_data": {},
                    },
                ),
                _response("已完成核查，详见 Artifact。"),
            ],
            "verification": [
                _response(
                    json.dumps(
                        {
                            "cards": [
                                {
                                    "claim": "官方已发布阶段性说明",
                                    "verdict": "supported",
                                    "confidence": 0.8,
                                    "reason": "声明与机构发布帖一致",
                                    "supporting_evidence": ["social_post:{db}"],
                                    "contradicting_evidence": [],
                                }
                            ],
                            "limitations": ["仅 6 条 demo 帖子"],
                        }
                    ).replace("{db}", "placeholder")
                ),
            ],
        }
    )
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'expert_fc.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="核查落库", platforms=["weibo", "bilibili"])
        )
        await social.persist_batch(case_id=case.id, posts=_POSTS)
        posts = await social.list_posts_by_case(case.id)
        db_id = posts[0].id
        # 把占位符替换为真实 db id（social_post:{db_id} 前缀格式）
        gateway._scripts["verification"][0] = _response(
            json.dumps(
                {
                    "cards": [
                        {
                            "claim": "官方已发布阶段性说明",
                            "verdict": "supported",
                            "confidence": 0.8,
                            "reason": "声明与机构发布帖一致",
                            "supporting_evidence": [f"social_post:{db_id}"],
                            "contradicting_evidence": [],
                        }
                    ],
                    "limitations": ["仅 6 条 demo 帖子"],
                }
            )
        )
        worker, repository, _ = await _build_worker(
            tmp_path,
            gateway,
            social=social,
            database=database,
        )
        service = AgentRunService(repository, worker)
        run = await service.start(
            case_id=case.id,
            content="核查主张",
            approve_crawl=True,
        )
        await worker.start()
        try:
            await _wait_for_run(repository, run.id)
        finally:
            await worker.stop()

        claims = await repository.list_claims_by_case(case.id)
        assert len(claims) == 1
        assert claims[0].verdict == "supported"
        assert claims[0].confidence == 0.8
        evidence = await repository.list_evidence_by_claim(claims[0].id)
        assert len(evidence) == 1
        assert evidence[0].stance == "support"
        assert evidence[0].source_id == db_id
    finally:
        await database.dispose()


def test_every_expert_kind_has_artifact_kind() -> None:
    from app.application.graph_worker import _EXPERT_ARTIFACT_KINDS

    assert set(_EXPERT_ARTIFACT_KINDS) == {kind.value for kind in ExpertKind}


async def test_dispatch_emits_expert_dispatched_on_parent(tmp_path: Path) -> None:
    gateway = ScriptedGateway(
        {
            "coordinator": [
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "opinion",
                        "instructions": "分析观点。",
                        "input_data": {},
                    },
                ),
                _response("完成。"),
            ],
            "opinion": [
                _response('{"conclusions": [], "statistics": {"total_posts": 0}}'),
            ],
        }
    )
    worker, repository, database = await _build_worker(tmp_path, gateway)
    service = AgentRunService(repository, worker)
    case = await repository.create_case(
        CreateCaseRequest(topic="委派事件", platforms=["weibo"])
    )
    run = await service.start(
        case_id=case.id, content="分析", approve_crawl=True
    )
    await worker.start()
    try:
        await _wait_for_run(repository, run.id)
    finally:
        await worker.stop()
        await database.dispose()

    events = await repository.list_run_events(run.id)
    types = [event.event_type for event in events]
    assert "expert_dispatched" in types
    dispatched = next(
        event for event in events if event.event_type == "expert_dispatched"
    )
    assert dispatched.payload.get("agent") == "opinion"
    assert dispatched.payload.get("child_run_id")
    assert "expert_completed" in types


async def test_failed_expert_notifies_parent(tmp_path: Path) -> None:
    class FailingOpinionGateway(ScriptedGateway):
        async def complete(self, *, messages, tools, route, temperature=0):
            agent = self._detect_agent(messages[0].content or "")
            if agent == "opinion":
                from app.core.errors import ApplicationError

                raise ApplicationError("专家内部错误", code="expert_boom")
            return await super().complete(
                messages=messages, tools=tools, route=route, temperature=temperature
            )

    gateway = FailingOpinionGateway(
        {
            "coordinator": [
                _tool_response(
                    "dispatch_expert",
                    {
                        "agent": "opinion",
                        "instructions": "分析观点。",
                        "input_data": {},
                    },
                ),
                _response("专家失败，已改用已有数据结束。"),
            ],
        }
    )
    worker, repository, database = await _build_worker(tmp_path, gateway)
    service = AgentRunService(repository, worker)
    case = await repository.create_case(
        CreateCaseRequest(topic="失败通知", platforms=["weibo"])
    )
    run = await service.start(
        case_id=case.id, content="分析", approve_crawl=True
    )
    await worker.start()
    try:
        finished = await _wait_for_run(repository, run.id)
    finally:
        await worker.stop()
        await database.dispose()

    assert finished.status == "completed"
    children = await repository.list_child_runs(run.id)
    assert children[0].status == "failed"
    events = await repository.list_run_events(run.id)
    assert any(event.event_type == "expert_failed" for event in events)
    mailbox = await repository.list_agent_messages(run.id)
    assert any(message.message_type == "expert_failed" for message in mailbox)
