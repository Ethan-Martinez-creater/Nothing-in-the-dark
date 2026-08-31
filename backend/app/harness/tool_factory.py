from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.application.platform_profile import PlatformProfileService
from app.application.ports.crawler import CrawlRequest, SocialCrawlerPort
from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.harness.agents import ExpertKind, build_definition_for
from app.harness.search_optimizer import (
    generate_platform_keywords,
    rewrite_search_query,
)
from app.harness.skills import SkillRegistry
from app.harness.tools import ToolRegistry, ToolSpec
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.embeddings import EmbeddingWorkerClient
from app.infrastructure.llm import LLMGateway
from app.infrastructure.sentiment import SentimentWorkerClient
from app.schemas.knowledge import CreateMemoryRequest
from app.services.analysis import (
    analyze_opinion,
    build_report,
    reconstruct_propagation,
    verify_claims,
)
from app.services.classifiers import ModelSentimentClassifier
from app.services.crawl_coverage import apply_coverage, format_coverage_memory
from app.services.collection_filters import (
    apply_collection_exclusions,
    validate_collection_filters,
)
from app.services.platform_comparison import build_platform_comparison

_DISPATCH_TIMEOUT_SECONDS = 600.0
_DISPATCH_POLL_SECONDS = 2.0
# Platform-level concurrency policy: at most this many concurrent crawls
# against the same platform (shared across all crawl tool calls).
_PLATFORM_CONCURRENCY = 2


class EvidenceHitOutput(BaseModel):
    evidence_id: str
    source_type: str
    source_id: str
    content: str
    score: float = 0
    retrieval_modes: list[str] = []
    platform: str | None = None
    source_url: str | None = None
    published_at: str | None = None

    model_config = {"extra": "allow"}


class SearchEvidenceOutput(BaseModel):
    available: bool
    hits: list[EvidenceHitOutput] = []

    model_config = {"extra": "allow"}


class ArtifactOutput(BaseModel):
    artifact_id: str
    case_id: str
    kind: str
    version: int
    title: str
    run_id: str | None = None
    data: dict[str, Any] = {}
    created_at: str | None = None

    model_config = {"extra": "allow"}


class GetArtifactOutput(BaseModel):
    ok: bool
    found: bool = False
    artifact: ArtifactOutput | None = None

    model_config = {"extra": "allow"}


class LoadSkillOutput(BaseModel):
    name: str
    available: bool
    manifest: dict[str, Any] | None = None
    instructions: str | None = None

    model_config = {"extra": "allow"}


class QueryClaimsOutput(BaseModel):
    ok: bool
    claims: list[dict[str, Any]] = []

    model_config = {"extra": "allow"}


class CrawlInput(BaseModel):
    case_id: str | None = None
    topic: str
    platforms: list[str]
    time_range: dict[str, str | None]
    limit_per_platform: int = Field(default=150, ge=1, le=600)
    per_day_limit: int = Field(default=150, ge=1, le=150)
    comment_limit: int = Field(default=10, ge=0, le=20)


class PostsInput(BaseModel):
    posts: list[dict[str, Any]]
    # Injected by the runtime; never model-controlled.
    case_id: str | None = None


class VerificationInput(PostsInput):
    topic: str
    # Injected by the runtime so claims persist with their creator run.
    run_id: str | None = None


class SentimentInput(BaseModel):
    posts: list[dict[str, Any]] = []
    texts: list[str] = []


class QueryClaimsInput(BaseModel):
    # Injected by the runtime; never model-controlled.
    case_id: str | None = None
    status: str | None = None
    limit: int = 50


class QueryEvidenceInput(BaseModel):
    # Injected by the runtime; never model-controlled.
    case_id: str | None = None
    claim_id: str | None = None
    source_type: str | None = None
    limit: int = 100


class QueryPropagationInput(BaseModel):
    # Injected by the runtime; never model-controlled.
    case_id: str | None = None
    relation: str | None = None
    min_confidence: float | None = None
    limit: int = 100


class ReportInput(BaseModel):
    topic: str
    opinion: dict[str, Any]
    propagation: dict[str, Any]
    fact_check: dict[str, Any]

class DispatchInput(BaseModel):
    agent: str
    instructions: str
    input_data: dict[str, Any] = {}
    # Injected by the runtime; never model-controlled.
    case_id: str | None = None
    run_id: str | None = None
    dispatch_key: str | None = None


class GetArtifactInput(BaseModel):
    case_id: str
    kind: str | None = None
    artifact_id: str | None = None


class SubmitReviewInput(BaseModel):
    object_type: str
    object_id: str
    summary: str = ""
    priority: int = 0
    risk_level: str = "low"
    # Injected by the runtime; never model-controlled.
    case_id: str | None = None


class LoadSkillInput(BaseModel):
    name: str


class SearchEvidenceInput(BaseModel):
    case_id: str
    query: str
    limit: int = 12
    platforms: list[str] | None = None
    time_range: dict[str, str | None] | None = None


class WriteMemoryInput(CreateMemoryRequest):
    case_id: str


_MCP_TOOL_PREFIX = "mcp:"


def _model_from_json_schema(
    name: str,
    schema: dict[str, Any] | None,
) -> type[BaseModel]:
    """Build a permissive pydantic model from an MCP tool's JSON Schema.

    Unknown property types degrade to ``Any`` so arguments pass through to
    the remote server; ``required`` fields still raise ValidationError
    before any network I/O (matching local tool semantics).
    """
    from pydantic import ConfigDict, create_model

    schema = schema or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields: dict[str, tuple[object, Any]] = {}
    type_map: dict[str, type[Any]] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for prop_name, prop in properties.items():
        prop_type = type_map.get(
            prop.get("type") if isinstance(prop, dict) else "",
            Any,
        )
        description = prop.get("description") if isinstance(prop, dict) else None
        if prop_name in required:
            fields[prop_name] = (
                prop_type,
                Field(description=description) if description else ...,
            )
        else:
            optional_type = prop_type | None
            fields[prop_name] = (
                optional_type,
                Field(default=None, description=description)
                if description
                else None,
            )
    return create_model(
        f"McpTool_{name}",
        __config__=ConfigDict(extra="allow", arbitrary_types_allowed=True),
        **fields,
    )


async def register_mcp_tools(
    registry: ToolRegistry,
    manager: Any,
    *,
    server_names: list[str] | None = None,
) -> list[str]:
    """Discover and register every allow-listed MCP tool.

    Registered names use the ``mcp:{server}:{tool}`` prefix so local and
    remote tools can never collide; all MCP tools are registered as
    idempotent, side-effect-free reads that go through the same
    permissions / timeout / cache / audit path as local tools.

    Returns the names actually registered. A failing server is skipped
    with a warning instead of blocking startup.
    """
    from app.mcp.client import McpClientManager

    if not isinstance(manager, McpClientManager):
        raise ApplicationError(
            "register_mcp_tools requires an McpClientManager",
            code="mcp_invalid_config",
        )
    registered: list[str] = []
    for server_name in server_names or manager.names():
        try:
            descriptors = await manager.discover_tools(server_name)
        except ApplicationError as exc:
            logging.getLogger(__name__).warning(
                "MCP server '%s' skipped: %s", server_name, exc
            )
            continue
        config = manager.config(server_name)
        for descriptor in descriptors:
            tool_name = f"{_MCP_TOOL_PREFIX}{server_name}:{descriptor.name}"
            if tool_name in registry.names():
                continue
            registry.register(
                ToolSpec(
                    name=tool_name,
                    version="1.0.0",
                    description=(
                        f"[MCP:{server_name}] {descriptor.description or descriptor.name}"
                    ),
                    input_model=_model_from_json_schema(
                        f"{server_name}_{descriptor.name}",
                        descriptor.input_schema,
                    ),
                    handler=(
                        lambda arguments, s=server_name, t=descriptor.name: manager.call_tool(
                            s, t, arguments.model_dump(exclude_none=True)
                        )
                    ),
                    permissions=("read_database",),
                    side_effect="none",
                    idempotent=True,
                    execution_mode="parallel",
                    timeout_seconds=int(config.timeout_seconds),
                    cache_ttl_seconds=30,
                )
            )
            registered.append(tool_name)
    return registered


def build_tool_registry(
    crawler: SocialCrawlerPort,
    skills: SkillRegistry | None = None,
    knowledge: KnowledgeRepository | None = None,
    embeddings: EmbeddingWorkerClient | None = None,
    social: SocialRepository | None = None,
    repository: ApplicationRepository | None = None,
    sentiment: SentimentWorkerClient | None = None,
    llm: LLMGateway | None = None,
    security: Any = None,
    governance: Any = None,
    collection_service: Any = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    platform_semaphores: dict[str, asyncio.Semaphore] = {}

    def _platform_semaphore(platform: str) -> asyncio.Semaphore:
        semaphore = platform_semaphores.get(platform)
        if semaphore is None:
            semaphore = asyncio.Semaphore(_PLATFORM_CONCURRENCY)
            platform_semaphores[platform] = semaphore
        return semaphore

    # 平台画像服务：采集入库后总结/更新平台与用户特点（无 LLM 或未配
    # knowledge 时跳过，画像刷新失败不阻断采集结果）。
    # M23: 画像经治理 Gate 落库（LLM 生成内容，低信任可审）。
    profile_service = (
        PlatformProfileService(knowledge, llm, governance=governance)
        if knowledge is not None and llm is not None
        else None
    )

    async def dispatch_expert(arguments: BaseModel) -> dict[str, Any]:
        request = DispatchInput.model_validate(arguments)
        if repository is None:
            return {
                "ok": False,
                "error": {
                    "code": "dispatch_unavailable",
                    "message": "Expert dispatch requires a repository.",
                },
            }
        if not request.run_id or not request.dispatch_key or not request.case_id:
            return {
                "ok": False,
                "error": {
                    "code": "invalid_request",
                    "message": "dispatch_expert requires a runtime-provided run scope.",
                },
            }
        try:
            ExpertKind(request.agent)
        except ValueError:
            return {
                "ok": False,
                "error": {
                    "code": "unknown_agent",
                    "message": f"Unknown expert agent '{request.agent}'.",
                },
            }
        child = await repository.get_child_run_by_dispatch_key(
            request.run_id,
            request.dispatch_key,
        )
        if child is None:
            definition = build_definition_for(request.agent)
            child = await repository.create_agent_run(
                case_id=request.case_id,
                turn_id=None,
                objective=request.instructions,
                agent=request.agent,
                model_route=definition.model_route.value,
                parent_run_id=request.run_id,
                metadata={
                    "dispatch": {
                        "dispatch_key": request.dispatch_key,
                        "input_data": request.input_data,
                    },
                    "approve_crawl": False,
                },
            )
            await repository.add_run_event(
                request.run_id,
                {
                    "event_type": "expert_dispatched",
                    "agent": request.agent,
                    "status": "running",
                    "child_run_id": child.id,
                    "dispatch_key": request.dispatch_key,
                },
            )
        return await _wait_for_child(repository, child)

    async def get_artifact(arguments: BaseModel) -> dict[str, Any]:
        request = GetArtifactInput.model_validate(arguments)
        if repository is None:
            return {
                "ok": False,
                "error": {
                    "code": "repository_unavailable",
                    "message": "Artifact lookup requires a repository.",
                },
            }
        if request.artifact_id:
            try:
                record = await repository.get_artifact(request.artifact_id)
            except ApplicationError:
                return {"ok": True, "found": False, "artifact": None}
            if record.case_id != request.case_id:
                return {
                    "ok": False,
                    "error": {
                        "code": "artifact_scope_mismatch",
                        "message": "Artifact belongs to a different case.",
                    },
                }
        elif request.kind:
            record = await repository.get_latest_artifact(
                request.case_id,
                request.kind,
            )
            if record is None:
                return {"ok": True, "found": False, "artifact": None}
        else:
            return {
                "ok": False,
                "error": {
                    "code": "invalid_request",
                    "message": "get_artifact requires kind or artifact_id.",
                },
            }
        return {
            "ok": True,
            "found": True,
            "artifact": {
                "artifact_id": record.id,
                "case_id": record.case_id,
                "kind": record.kind,
                "version": record.version,
                "title": record.title,
                "run_id": record.run_id,
                "data": record.data,
                "created_at": (
                    record.created_at.isoformat() if record.created_at else None
                ),
            },
        }

    async def crawl(arguments: BaseModel) -> dict[str, Any]:
        request = CrawlInput.model_validate(arguments)
        # M3: 优先使用该 case 的 Active Collection Definition（关键词/
        # 排除词由用户确认的定义提供）；无定义时回退既有 LLM 检索优化。
        # case_id 仍由 Runtime scope 注入；approval/sandbox 顺序完全不变。
        keywords: dict[str, list[str]] | None = None
        collection_ref: dict[str, Any] | None = None
        collection_exclusions: list[str] | None = None
        if request.case_id and collection_service is not None:
            try:
                active = await collection_service.get_active(request.case_id)
            except Exception:
                active = None
            if active is not None:
                keywords = collection_service.keywords_for(
                    active,
                    requested_platforms=list(request.platforms),
                    fallback_topic=request.topic,
                )
                collection_ref = {"id": active.id, "version": active.version}
                # C6：未知 filter key 运行时同样 fail closed（防御保存旁路）
                validate_collection_filters(active.filters)
                collection_exclusions = list(active.exclusions or [])
        if keywords is None:
            # LLM 检索优化：按平台特点生成多组检索关键词（失败回退 topic）。
            keywords = await generate_platform_keywords(
                llm, request.topic, request.platforms
            )

        # M15 强制沙箱：采集（外部副作用段）必须通过受限子进程执行；
        # 未装配沙箱执行器时 fail closed（绝不降级裸跑）。
        async def collect_platform(platform: str) -> list[dict[str, Any]]:
            async with _platform_semaphore(platform):
                external = await registry.run_external_tool(
                    "collect_social_posts",
                    {
                        "topic": request.topic,
                        "platforms": [platform],
                        "time_range": dict(request.time_range),
                        "limit_per_platform": request.limit_per_platform,
                        "per_day_limit": request.per_day_limit,
                        "comment_limit": request.comment_limit,
                        "keywords": {
                            platform: keywords.get(platform, [request.topic])
                        },
                    },
                )
                return list(external.get("posts") or [])

        platform_results = await asyncio.gather(
            *(collect_platform(platform) for platform in request.platforms)
        )
        raw_posts = [post for result in platform_results for post in result]
        # C6：active definition 的 exclusions 在 coverage/persistence 前过滤；
        # comment 跟随父记录。无 active definition 时保持旧路径。
        raw_posts, collection_filter_stats = apply_collection_exclusions(
            raw_posts, collection_exclusions
        )
        coverage = apply_coverage(
            raw_posts,
            CrawlRequest(
                topic=request.topic,
                platforms=list(request.platforms),
                time_range=request.time_range,
                limit_per_platform=request.limit_per_platform,
                per_day_limit=request.per_day_limit,
                comment_limit=request.comment_limit,
            ),
        )
        posts = coverage.posts
        persisted: dict[str, Any] | None = None
        if request.case_id and social is not None:
            result = await social.persist_batch(
                case_id=request.case_id,
                posts=posts,
            )
            persisted = {
                "posts_created": result.posts_created,
                "posts_updated": result.posts_updated,
                "comments_created": result.comments_created,
                "comments_updated": result.comments_updated,
                "raw_records_created": result.raw_records_created,
            }
            if repository is not None:
                try:
                    from app.application.domain_ingest import ingest_after_crawl

                    persisted["domain"] = await ingest_after_crawl(
                        repository, social, request.case_id, posts
                    )
                except Exception:
                    logging.getLogger(__name__).warning(
                        "domain ingest failed after crawl",
                        exc_info=True,
                    )
            # 采集入库后刷新平台画像记忆（LLM 总结 + 比较更新）；
            # 失败只跳过，不影响采集返回。
            if posts and profile_service is not None:
                try:
                    profile_status = await profile_service.refresh_from_posts(
                        request.platforms, posts, topic=request.topic
                    )
                    persisted["platform_profiles"] = profile_status
                except Exception:
                    logging.getLogger(__name__).warning(
                        "platform profile refresh failed after crawl",
                        exc_info=True,
                    )
            if knowledge is not None:
                try:
                    memory_text = format_coverage_memory(
                        request.topic, coverage.stats
                    )
                    await _persist_governed_memory(
                        governance,
                        knowledge,
                        request.case_id,
                        CreateMemoryRequest(
                            scope="case",
                            kind="fact",
                            content=memory_text,
                            source_type="crawl_coverage",
                            source_id=f"crawl-coverage:{request.case_id}",
                            importance=0.72,
                            confidence=1.0,
                            metadata=coverage.stats.to_dict(),
                        ),
                        memory_type="case_fact",
                        trust_level="tool_diagnostic",
                    )
                    if coverage.stats.special_terms:
                        terms = "、".join(
                            item["term"] for item in coverage.stats.special_terms
                        )
                        await _persist_governed_memory(
                            governance,
                            knowledge,
                            request.case_id,
                            CreateMemoryRequest(
                                scope="case",
                                kind="constraint",
                                content=(
                                    f"主题「{request.topic}」评论区出现偏离字面义的"
                                    f"高频用词：{terms}。分析结论时应结合语境，"
                                    "勿按词典义直接当作立场或情感。"
                                ),
                                source_type="crawl_coverage",
                                source_id=f"crawl-special-terms:{request.case_id}",
                                importance=0.8,
                                confidence=0.7,
                                metadata={"special_terms": coverage.stats.special_terms},
                            ),
                            memory_type="case_hypothesis",
                            trust_level="tool_diagnostic",
                        )
                except Exception:
                    logging.getLogger(__name__).warning(
                        "crawl coverage memory persist failed",
                        exc_info=True,
                    )
        public_posts = [
            {
                key: value
                for key, value in post.items()
                if key not in {"raw", "comments"}
            }
            for post in posts
        ]
        comment_count = 0
        for post in posts:
            comments = post.get("comments")
            if isinstance(comments, list):
                comment_count += len(comments)
        result: dict[str, Any] = {
            "posts": public_posts,
            "comment_count": comment_count,
            "persistence": persisted,
            "coverage": coverage.stats.to_dict(),
        }
        # M3: 采集定义审计引用（使用 Active Definition 时附带 id/version）。
        if collection_ref is not None:
            result["collection_definition"] = collection_ref
            # C6: exclusions 过滤审计（before/after/excluded）。
            result["collection_filter_stats"] = collection_filter_stats
        return result

    async def classify_sentiment(arguments: BaseModel) -> dict[str, Any]:
        request = SentimentInput.model_validate(arguments)
        texts = [str(post.get("content") or "") for post in request.posts]
        if request.texts:
            texts = request.texts
        if not texts:
            return {
                "ok": False,
                "error": {
                    "code": "invalid_request",
                    "message": "classify_sentiment requires posts or texts.",
                },
            }
        classifier = ModelSentimentClassifier(sentiment)
        results = [item.to_dict() for item in await classifier.classify_batch(texts)]
        if request.posts:
            for post, item in zip(request.posts, results, strict=True):
                item["post_id"] = str(post.get("id") or "")
        return {"results": results, "source": results[0]["source"] if results else "dictionary"}

    async def opinion(arguments: BaseModel) -> dict[str, Any]:
        request = PostsInput.model_validate(arguments)
        classifier = ModelSentimentClassifier(sentiment)
        classifications = await classifier.classify_batch(
            [str(post.get("content") or "") for post in request.posts]
        )
        return analyze_opinion(request.posts, classifications=classifications)

    async def compare_platforms_handler(arguments: BaseModel) -> dict[str, Any]:
        request = PostsInput.model_validate(arguments)
        return build_platform_comparison(request.posts)

    async def propagation(arguments: BaseModel) -> dict[str, Any]:
        request = PostsInput.model_validate(arguments)
        graph = await reconstruct_propagation(
            request.posts,
            embedding_client=embeddings,
            llm=llm,
        )
        await _persist_propagation_edges(
            graph,
            repository=repository,
            social=social,
            case_id=request.case_id,
        )
        return graph

    async def verification(arguments: BaseModel) -> dict[str, Any]:
        request = VerificationInput.model_validate(arguments)
        return await verify_claims(
            request.posts,
            request.topic,
            repository=repository,
            case_id=request.case_id,
            created_by_run_id=request.run_id,
        )

    async def query_claims(arguments: BaseModel) -> dict[str, Any]:
        request = QueryClaimsInput.model_validate(arguments)
        if repository is None or not request.case_id:
            return {
                "ok": False,
                "error": {
                    "code": "unavailable",
                    "message": "Claim lookup requires a case-scoped repository.",
                },
            }
        claims = await repository.list_claims_by_case(
            request.case_id,
            status=request.status,
            limit=request.limit,
        )
        return {
            "ok": True,
            "claims": [
                {
                    "claim_id": claim.id,
                    "case_id": claim.case_id,
                    "text": claim.text,
                    "status": claim.status,
                    "verdict": claim.verdict,
                    "confidence": claim.confidence,
                    "created_at": claim.created_at.isoformat() if claim.created_at else None,
                }
                for claim in claims
            ],
        }

    async def query_evidence(arguments: BaseModel) -> dict[str, Any]:
        request = QueryEvidenceInput.model_validate(arguments)
        if repository is None or not (request.case_id or request.claim_id):
            return {
                "ok": False,
                "error": {
                    "code": "unavailable",
                    "message": "Evidence lookup requires a repository.",
                },
            }
        if request.claim_id:
            evidence = await repository.list_evidence_by_claim(request.claim_id)
        elif request.case_id:
            evidence = await repository.list_evidence_by_case(
                request.case_id,
                source_type=request.source_type,
                limit=request.limit,
            )
        else:  # pragma: no cover - guarded above
            evidence = []
        return {
            "ok": True,
            "evidence": [
                {
                    "evidence_id": item.id,
                    "case_id": item.case_id,
                    "claim_id": item.claim_id,
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "stance": item.stance,
                    "excerpt": item.excerpt,
                    "relevance": item.relevance,
                }
                for item in evidence
            ],
        }

    async def query_propagation(arguments: BaseModel) -> dict[str, Any]:
        request = QueryPropagationInput.model_validate(arguments)
        if repository is None or not request.case_id:
            return {
                "ok": False,
                "error": {
                    "code": "unavailable",
                    "message": "Propagation lookup requires a case-scoped repository.",
                },
            }
        edges = await repository.list_propagation_edges_by_case(
            request.case_id,
            relation=request.relation,
            min_confidence=request.min_confidence,
            limit=request.limit,
        )
        return {
            "ok": True,
            "edges": [
                {
                    "edge_id": edge.id,
                    "source_post_id": edge.source_post_id,
                    "target_post_id": edge.target_post_id,
                    "relation": edge.relation,
                    "confidence": edge.confidence,
                    "feature_scores": edge.feature_scores,
                    "evidence_ids": edge.evidence_ids,
                    "algorithm_version": edge.algorithm_version,
                    "human_confirmed": edge.human_confirmed,
                }
                for edge in edges
            ],
        }

    async def report(arguments: BaseModel) -> dict[str, Any]:
        request = ReportInput.model_validate(arguments)
        return build_report(
            request.topic,
            request.opinion,
            request.propagation,
            request.fact_check,
        )

    async def load_skill(arguments: BaseModel) -> dict[str, Any]:
        request = LoadSkillInput.model_validate(arguments)
        if skills is None:
            return {"name": request.name, "available": False}
        # 结构化返回：manifest 元数据（版本/契约/成本/取消策略）+ 指令正文
        return {
            "name": request.name,
            "available": True,
            "manifest": skills.describe_one(request.name),
            "instructions": skills.load(request.name),
        }

    async def search_evidence(arguments: BaseModel) -> dict[str, Any]:
        request = SearchEvidenceInput.model_validate(arguments)
        if knowledge is None:
            return {"hits": [], "available": False}
        # LLM 检索优化：重写/扩写检索词（失败回退原始 query）。
        query = await rewrite_search_query(llm, request.query)
        query_vectors = (
            await embeddings.embed([query])
            if embeddings is not None
            else None
        )
        time_from = time_to = None
        if request.time_range:
            try:
                if request.time_range.get("from"):
                    time_from = datetime.fromisoformat(request.time_range["from"])
                if request.time_range.get("to"):
                    time_to = datetime.fromisoformat(request.time_range["to"])
            except ValueError:
                return {
                    "ok": False,
                    "error": {
                        "code": "invalid_time_range",
                        "message": "time_range must contain ISO-8601 dates.",
                    },
                }
        hits = await knowledge.search(
            case_id=request.case_id,
            query=query,
            limit=request.limit,
            embedding=query_vectors[0] if query_vectors else None,
            platforms=request.platforms,
            time_from=time_from,
            time_to=time_to,
        )
        return {
            "available": True,
            "hits": [
                {
                    "evidence_id": hit.evidence_id,
                    "source_type": hit.source_type,
                    "source_id": hit.source_id,
                    "content": hit.content,
                    "score": hit.score,
                    "retrieval_modes": hit.retrieval_modes,
                    "platform": hit.platform,
                    "source_url": hit.source_url,
                    "published_at": (
                        hit.published_at.isoformat() if hit.published_at else None
                    ),
                }
                for hit in hits
            ],
        }

    async def submit_review_item(arguments: BaseModel) -> dict[str, Any]:
        request = SubmitReviewInput.model_validate(arguments)
        if repository is None or not request.case_id:
            return {
                "ok": False,
                "error": {
                    "code": "unavailable",
                    "message": "Review submission requires a case-scoped repository.",
                },
            }
        from app.infrastructure.database.models import ReviewItemRecord

        try:
            # RC1: finding 必须走唯一原子提交入口（验证 Finding + case scope +
            # 状态行为表 + 单事务），不得在 tool 层复制第二套创建逻辑。
            if request.object_type == "finding":
                finding, item = await repository.submit_finding_for_review(
                    case_id=request.case_id,
                    finding_id=request.object_id,
                    priority=request.priority,
                    risk_level=request.risk_level,
                    actor="agent_review_submit",
                )
                return {
                    "ok": True,
                    "item_id": item.id,
                    "status": item.status,
                    "finding_status": finding.status,
                }
            existing = await repository.list_review_items(request.case_id, limit=1000)
            for item in existing:
                if (
                    item.object_type == request.object_type
                    and item.object_id == request.object_id
                ):
                    return {
                        "ok": True,
                        "item_id": item.id,
                        "status": item.status,
                        "already_exists": True,
                    }
            item = await repository.create_review_item(
                ReviewItemRecord(
                    case_id=request.case_id,
                    object_type=request.object_type,
                    object_id=request.object_id,
                    summary=request.summary,
                    priority=request.priority,
                    risk_level=request.risk_level,
                )
            )
            return {"ok": True, "item_id": item.id, "status": item.status}
        except Exception as exc:
            return {
                "ok": False,
                "error": {"code": "review_submit_failed", "message": str(exc)[:300]},
            }

    async def write_memory(arguments: BaseModel) -> dict[str, Any]:
        request = WriteMemoryInput.model_validate(arguments)
        if knowledge is None:
            return {"available": False}
        # M16: 记忆写入门——外部来源且未审核的内容默认拒绝写入长期记忆。
        if security is not None and request.source_type in {
            "social_post",
            "social_comment",
            "document_chunk",
        }:
            gate = await security.check_memory_write(
                request.content,
                source_type=request.source_type,
                source_id=request.source_id,
                trust="external_content",
                review_state="unreviewed",
            )
            if gate["decision"] == "deny":
                return {
                    "available": False,
                    "ok": False,
                    "error": {
                        "code": "memory_write_blocked",
                        "message": gate["reason"],
                    },
                }
        payload = CreateMemoryRequest.model_validate(
            request.model_dump(exclude={"case_id"})
        )
        memory_vectors = (
            await embeddings.embed([request.content])
            if embeddings is not None
            else None
        )
        # M23: 模型写入走治理 Gate——外部来源内容不能直接成为高信任事实。
        external = request.source_type in {
            "social_post",
            "social_comment",
            "document_chunk",
        }
        record = await _persist_governed_memory(
            governance,
            knowledge,
            request.case_id,
            payload,
            memory_type="case_hypothesis" if external else "case_fact",
            trust_level=(
                "external_content" if external else "generated_content"
            ),
            has_evidence=not external,
            embedding=memory_vectors[0] if memory_vectors else None,
        )
        return {
            "available": True,
            "memory_id": record.id,
            "active": record.active,
            "supersedes_id": record.supersedes_id,
            "status": getattr(record, "status", None),
        }

    registry.register(
        ToolSpec(
            name="load_skill",
            version="1.0.0",
            description="Load a domain skill's full instructions on demand.",
            input_model=LoadSkillInput,
            handler=load_skill,
            permissions=("read_skill",),
            output_model=LoadSkillOutput,
        )
    )
    registry.register(
        ToolSpec(
            name="search_social_evidence",
            version="1.0.0",
            description=(
                "Search case-scoped social posts, uploaded documents, and sourced "
                "memory. Returns stable evidence IDs for citation."
            ),
            input_model=SearchEvidenceInput,
            handler=search_evidence,
            permissions=("read_database",),
            side_effect="none",
            idempotent=True,
            execution_mode="parallel",
            output_model=SearchEvidenceOutput,
            cache_ttl_seconds=60,
            rag_output=True,
        )
    )
    registry.register(
        ToolSpec(
            name="write_case_memory",
            version="1.0.0",
            description=(
                "Persist a sourced, case-scoped memory. Model inference must not be "
                "stored as a fact without a source."
            ),
            input_model=WriteMemoryInput,
            handler=write_memory,
            permissions=("write_memory",),
            side_effect="database_write",
            idempotent=True,
            execution_mode="sequential",
        )
    )
    registry.register(
        ToolSpec(
            name="collect_social_posts",
            version="0.1.0",
            description=(
                "Collect normalized social posts through the configured crawler. "
                "Post-filters results to the case time range and reports empty days; "
                "platform search APIs do not guarantee complete historical coverage. "
                "Each day keeps "
                "up to 150 ranked posts per platform after dropping short/near-dup "
                "items. Each post keeps at most 10 ranked comments. Returns "
                "coverage stats (raw/kept/empty days/special terms)."
            ),
            input_model=CrawlInput,
            handler=crawl,
            permissions=("crawl_platform", "write_database"),
            side_effect="external_read",
            idempotent=False,
            timeout_seconds=600,
            requires_approval=True,
            execution_mode="sequential",
            max_concurrency=1,
            # M15: 外部爬虫属于受限进程执行类，网络按平台画像白名单，
            # Cookie 经 SecretProvider 注入，高风险需审批。
            execution_class="restricted_process",
            network={
                "mode": "platform_profile",
                "domains": [
                    "weibo.com",
                    "weibo.cn",
                    "sina.com.cn",
                    "sinaimg.cn",
                    "bilibili.com",
                    "hdslb.com",
                    "tieba.baidu.com",
                    "baidu.com",
                    "bdstatic.com",
                    "zhihu.com",
                    "zhimg.com",
                    "douyin.com",
                    "douyinpic.com",
                    "douyincdn.com",
                ],
            },
            secrets=(
                "MEDIACRAWLER_WEIBO_COOKIES",
                "MEDIACRAWLER_BILIBILI_COOKIES",
                "MEDIACRAWLER_TIEBA_COOKIES",
                "MEDIACRAWLER_ZHIHU_COOKIES",
                "MEDIACRAWLER_DOUYIN_COOKIES",
            ),
            resources={"timeout_seconds": 600},
            risk_level="high",
        )
    )
    registry.register(
        ToolSpec(
            name="dispatch_expert",
            version="1.0.0",
            description=(
                "Delegate a sub-analysis to an expert agent (opinion, propagation, "
                "verification, evidence_critic, report, citation_validator) and wait "
                "for its structured artifact."
            ),
            input_model=DispatchInput,
            handler=dispatch_expert,
            permissions=("read_database", "read_artifact", "write_database"),
            side_effect="database_write",
            idempotent=True,
            timeout_seconds=660,
            execution_mode="parallel",
        )
    )
    registry.register(
        ToolSpec(
            name="get_artifact",
            version="1.0.0",
            description=(
                "Read a persisted artifact (by id or the latest version of a kind) "
                "for the current case."
            ),
            input_model=GetArtifactInput,
            handler=get_artifact,
            permissions=("read_artifact",),
            side_effect="none",
            idempotent=True,
            execution_mode="parallel",
            output_model=GetArtifactOutput,
            cache_ttl_seconds=30,
        )
    )
    registry.register(
        ToolSpec(
            name="classify_sentiment",
            version="1.0.0",
            description=(
                "Classify post contents (or raw texts) into sentiment and stance "
                "using the local sentiment model, with a deterministic dictionary "
                "fallback. Returns per-item sentiment, score, confidence and stance."
            ),
            input_model=SentimentInput,
            handler=classify_sentiment,
            side_effect="none",
            idempotent=True,
            execution_mode="parallel",
        )
    )
    registry.register(
        ToolSpec(
            name="query_claims",
            version="1.0.0",
            description=(
                "List persisted claims of the current case (optionally filtered "
                "by status). Returns real claim ids for citation."
            ),
            input_model=QueryClaimsInput,
            handler=query_claims,
            permissions=("read_database",),
            side_effect="none",
            idempotent=True,
            execution_mode="parallel",
            output_model=QueryClaimsOutput,
            cache_ttl_seconds=30,
        )
    )
    registry.register(
        ToolSpec(
            name="query_evidence",
            version="1.0.0",
            description=(
                "List evidence rows for a claim or a case (optionally filtered "
                "by source type). Returns real evidence ids for citation."
            ),
            input_model=QueryEvidenceInput,
            handler=query_evidence,
            permissions=("read_database",),
            side_effect="none",
            idempotent=True,
            execution_mode="parallel",
        )
    )
    registry.register(
        ToolSpec(
            name="query_propagation",
            version="1.0.0",
            description=(
                "List persisted propagation edges of the current case with their "
                "feature scores, confidence and algorithm version."
            ),
            input_model=QueryPropagationInput,
            handler=query_propagation,
            permissions=("read_database",),
            side_effect="none",
            idempotent=True,
            execution_mode="parallel",
        )
    )
    registry.register(
        ToolSpec(
            name="analyze_opinion",
            version="1.0.0",
            description="Calculate sentiment and platform distributions from posts.",
            input_model=PostsInput,
            handler=opinion,
        )
    )
    registry.register(
        ToolSpec(
            name="reconstruct_propagation",
            version="0.1.0",
            description="Build observed and inferred cross-platform propagation edges.",
            input_model=PostsInput,
            handler=propagation,
        )
    )
    registry.register(
        ToolSpec(
            name="verify_claims",
            version="0.1.0",
            description="Verify claims within the bounded social evidence set.",
            input_model=VerificationInput,
            handler=verification,
        )
    )
    registry.register(
        ToolSpec(
            name="build_report",
            version="0.1.0",
            description="Build a structured first-version report artifact.",
            input_model=ReportInput,
            handler=report,
        )
    )
    registry.register(
        ToolSpec(
            name="compare_platforms",
            version="1.0.0",
            description="Compare how the same event unfolded across platforms: "
            "participation, sentiment, timeline, common terms and insights.",
            input_model=PostsInput,
            handler=compare_platforms_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="submit_review_item",
            version="1.0.0",
            description=(
                "Submit a candidate (claim/evidence/edge/risk/etc.) to the "
                "human review queue. Agents never write review decisions directly."
            ),
            input_model=SubmitReviewInput,
            handler=submit_review_item,
            permissions=("write_database",),
            side_effect="database_write",
            idempotent=True,
            execution_mode="sequential",
        )
    )
    return registry


def _resolve_graph_post_id(
    reference: str,
    posts: Sequence[Any],
    native_to_db: dict[str, str],
) -> str | None:
    """Resolve a graph edge endpoint to a persisted source_post id.

    Edges reference posts in two shapes: the raw native id (demo or crawler
    origin, e.g. "demo-weibo-1") and the prefixed database id produced by
    the RAG/query paths ("social_post:{db_id}").
    """
    if reference.startswith("social_post:"):
        db_id = reference[len("social_post:"):]
        for post in posts:
            if post.id == db_id:
                return post.id
        return None
    return native_to_db.get(reference)


async def _persist_propagation_edges(
    graph: dict[str, Any],
    *,
    repository: ApplicationRepository | None,
    social: SocialRepository | None,
    case_id: str | None,
) -> None:
    """Persist graph edges mapped to real source_post ids.

    Endpoints are accepted as native ids ("demo-weibo-1") or prefixed
    database ids ("social_post:{db_id}"); edges whose endpoints are not
    among the case's persisted posts are skipped; persisted edges are
    idempotent on (case, source, target).
    """
    if repository is None or social is None or not case_id:
        return
    posts = await social.list_posts_by_case(case_id)
    native_to_db = {str(post.native_id): post.id for post in posts}
    for edge in graph.get("edges", []):
        source_db = _resolve_graph_post_id(str(edge.get("source") or ""), posts, native_to_db)
        target_db = _resolve_graph_post_id(str(edge.get("target") or ""), posts, native_to_db)
        if not source_db or not target_db:
            continue
        record = await repository.create_propagation_edge(
            case_id=case_id,
            source_post_id=source_db,
            target_post_id=target_db,
            relation=str(edge.get("relation") or "inferred"),
            confidence=float(edge.get("confidence") or 0.0),
            feature_scores=dict(edge.get("feature_scores") or {}),
            evidence_ids=list(edge.get("evidence_ids") or []),
            algorithm_version=str(edge.get("algorithm_version") or "1.0.0"),
        )
        # 回填数据库边 id：前端传播边确认按钮依赖它定位边（幂等时复用
        # 已存在行，id 保持稳定）。
        edge["edge_id"] = record.id
    try:
        from app.application.domain_ingest import ingest_propagation_nodes

        graph["nodes_persisted"] = await ingest_propagation_nodes(
            repository, social, case_id, graph
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "propagation node ingest failed",
            exc_info=True,
        )


async def _wait_for_child(
    repository: ApplicationRepository,
    child: Any,
) -> dict[str, Any]:
    """Wait for a child expert run to finish and summarize its artifacts."""
    deadline = time.monotonic() + _DISPATCH_TIMEOUT_SECONDS
    while True:
        run = await repository.get_agent_run(child.id)
        if run.status == "completed":
            artifacts = await repository.list_run_artifacts(run.id)
            return {
                "ok": True,
                "child_run_id": run.id,
                "agent": run.agent,
                "status": "completed",
                "artifacts": [
                    {
                        "artifact_id": artifact.id,
                        "kind": artifact.kind,
                        "version": artifact.version,
                        "title": artifact.title,
                        "data": artifact.data,
                    }
                    for artifact in artifacts
                ],
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "estimated_cost": run.estimated_cost,
            }
        if run.status in {"failed", "cancelled"}:
            return {
                "ok": False,
                "child_run_id": run.id,
                "agent": run.agent,
                "status": run.status,
                "error": run.error,
            }
        if time.monotonic() >= deadline:
            return {
                "ok": False,
                "child_run_id": run.id,
                "agent": run.agent,
                "status": "timeout",
                "error": "Expert run did not complete within the dispatch window.",
            }
        await asyncio.sleep(_DISPATCH_POLL_SECONDS)


async def _persist_governed_memory(
    governance: Any,
    knowledge: KnowledgeRepository,
    case_id: str | None,
    request: CreateMemoryRequest,
    *,
    memory_type: str,
    trust_level: str,
    has_evidence: bool = False,
    embedding: list[float] | None = None,
) -> Any:
    """M23 治理化记忆写入：装配 governance 时经 WriteGate 落库（外部/生成
    内容不自行提升信任等级）；未装配时回退原路径（兼容测试与独立构造）。"""
    if governance is not None:
        return await governance.persist_governed(
            case_id=case_id,
            request=request,
            memory_type=memory_type,
            trust_level=trust_level,
            has_evidence=has_evidence,
            embedding=embedding,
        )
    return await knowledge.create_memory(case_id, request, embedding=embedding)
