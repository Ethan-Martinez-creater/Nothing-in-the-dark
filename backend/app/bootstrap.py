from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from app.a2a import LocalAgentGateway, RemoteAgentGateway
from app.application.agent_service import AgentRunService
from app.application.alignment_service import AlignmentService
from app.application.analysis_job_worker import AnalysisJobWorker
from app.application.authorization_service import AuthorizationService
from app.application.context_builder import ContextBuilder
from app.application.conversation_summary import ConversationSummarizer
from app.application.debate_service import DebateService
from app.application.evaluation_service import EvaluationService
from app.application.goal_service import GoalService
from app.application.graph_worker import GraphWorker
from app.application.integrity_service import IntegrityService
from app.application.media_pipeline_worker import MediaPipelineWorker
from app.application.memory_extraction import (
    CaseMemoryExtractor,
    MemoryExtractionService,
)
from app.application.memory_governance import MemoryGovernanceService
from app.application.monitor_scheduler import MonitorScheduler
from app.application.notification_service import (
    NotificationDispatcher,
    NotificationService,
)
from app.application.platform_profile import PlatformProfileService
from app.application.repositories import ApplicationRepository
from app.application.resilience_service import ResilienceService
from app.application.review_service import ReviewService
from app.application.runner import AnalysisRunner
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.graphs.case_analysis import CaseAnalysisGraph
from app.harness.egress_proxy import EgressProxy
from app.harness.sandbox import (
    SandboxedToolExecutor,
    SecretProvider,
    ToolPolicyEngine,
    build_sandbox_executor,
    container_supported,
)
from app.harness.skills import SkillRegistry
from app.harness.tool_factory import build_tool_registry, register_mcp_tools
from app.infrastructure.crawler import (
    DemoCrawlerAdapter,
    MediaCrawlerAdapter,
    MediaCrawlerConfig,
)
from app.infrastructure.database import Database
from app.infrastructure.database.alignment_repository import AlignmentRepository
from app.infrastructure.database.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.database.integrity_repository import IntegrityRepository
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.media_pipeline_repository import MediaPipelineRepository
from app.infrastructure.database.monitor_repository import MonitorRepository
from app.infrastructure.database.resilience_repository import ResilienceRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.database.uncertainty_repository import UncertaintyRepository
from app.infrastructure.embeddings import EmbeddingWorkerClient
from app.infrastructure.llm import OpenAICompatibleGateway
from app.infrastructure.media_fetch import MediaFetchService
from app.infrastructure.media_providers import (
    C2PAToolProvider,
    FFmpegFrameExtractor,
    FFprobeProvider,
    TesseractOCRProvider,
    WhisperASRProvider,
    probe_capabilities,
)
from app.infrastructure.sentiment import SentimentWorkerClient
from app.mcp.client import McpClientManager
from app.services.content_security import ContentSecurityService
from app.telemetry import build_telemetry

# 系统已知权限（各 ToolSpec.permissions 的并集）；Skill manifest 声明的
# 权限必须属于该白名单，M5 启动预检用。
_KNOWN_PERMISSIONS = frozenset(
    {
        "read_skill",
        "read_database",
        "write_memory",
        "crawl_platform",
        "write_database",
        "read_artifact",
        "write_artifact",
    }
)


async def create_checkpointer(database_url: str) -> tuple[Any, Any]:
    """Return ``(saver, context_manager)`` for the configured database.

    PostgreSQL gets a durable AsyncPostgresSaver; SQLite falls back to an
    in-memory saver so local development keeps working. The context manager
    owns the underlying connection and must be closed at shutdown.
    """
    if database_url.startswith("postgresql"):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        conninfo = database_url.replace("+asyncpg", "", 1)
        cm = AsyncPostgresSaver.from_conn_string(conninfo)
        saver = await cm.__aenter__()
        return saver, cm
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver(), None


class ApplicationContainer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # M19: 端到端可观测性（no-op/console/in_memory，不阻塞业务）。
        self.telemetry = build_telemetry(
            exporter_kind=settings.telemetry_exporter,
            otlp_endpoint=settings.telemetry_otlp_endpoint,
            otlp_service_name=settings.telemetry_otlp_service_name,
        )
        self.database = Database(settings.database_url)
        self.repository = ApplicationRepository(self.database)
        self.knowledge = KnowledgeRepository(self.database)
        self.social = SocialRepository(self.database)
        self.embeddings = EmbeddingWorkerClient(
            settings.embedding_worker_url,
            dimensions=settings.embedding_dimensions,
            timeout_seconds=settings.embedding_timeout_seconds,
        )
        # The ML worker serves both embeddings and sentiment from the same
        # process; the sentiment client reuses its base URL.
        self.sentiment = SentimentWorkerClient(
            settings.embedding_worker_url,
            timeout_seconds=settings.embedding_timeout_seconds,
        )
        self.crawler = self._build_crawler(settings)
        self.skills = self._build_skills()
        self.llm = OpenAICompatibleGateway(settings, telemetry=self.telemetry)
        # M16: 内容安全服务在工具注册之前装配（MemoryWriteGate 依赖）。
        async def _record_content_security(record: dict[str, object]) -> None:
            """持久化护栏决策与内容评估；失败静默降级，不阻断工具。"""
            try:
                assessment = record.get("assessment") or {}
                await self.repository.add_content_security_assessment(
                    object_type=str(assessment.get("object_type") or "content"),
                    object_id=str(assessment.get("object_id") or ""),
                    run_id=record.get("run_id"),
                    trust_level=str(assessment.get("trust_level") or "external_content"),
                    classification="content_security",
                    score=float(assessment.get("score") or 0),
                    risk_signals=list(assessment.get("signals") or []),
                    detector=str(assessment.get("detector") or "content_security_detectors"),
                    detector_version=str(assessment.get("detector_version") or "1.0"),
                    disposition=str(assessment.get("disposition") or "allowed"),
                    reason=str(assessment.get("reason") or ""),
                    content_hash=str(assessment.get("content_hash") or ""),
                    source_type=str(assessment.get("object_type") or "content"),
                    review_state=(
                        "accepted" if bool(assessment.get("reviewed")) else "unreviewed"
                    ),
                )
                await self.repository.add_guardrail_decision(
                    stage=str(record.get("stage") or ""),
                    run_id=record.get("run_id"),
                    turn_id=record.get("turn_id"),
                    tool_call_id=record.get("tool_call_id"),
                    tool=record.get("tool"),
                    decision=str(record.get("decision") or "allow"),
                    reason=str(record.get("reason") or ""),
                    policy_version=str(
                        record.get("policy_version")
                        or settings.content_security_policy_version
                    ),
                    signal_ids=list(record.get("signal_ids") or []),
                    content_hash=str(record.get("content_hash") or ""),
                    summary=str(record.get("summary") or ""),
                )
            except Exception:  # noqa: BLE001 - 观测持久化失败不得阻断执行
                pass

        self.content_security = ContentSecurityService(
            mode=settings.content_security_mode,
            policy_version=settings.content_security_policy_version,
            recorder=_record_content_security,
        )
        # M23: 记忆治理在工具注册之前装配（写入类工具经治理 Gate 落库）。
        self.memory_governance = MemoryGovernanceService(
            self.knowledge,
            security=self.content_security,
            telemetry=self.telemetry,
            write_policy_version=settings.memory_governance_policy_version,
        )
        self.tools = build_tool_registry(
            self.crawler,
            self.skills,
            self.knowledge,
            self.embeddings,
            self.social,
            self.repository,
            self.sentiment,
            self.llm,
            security=self.content_security,
            governance=self.memory_governance,
        )
        execution_class = settings.tool_sandbox_execution
        if execution_class == "container" and not container_supported():
            raise RuntimeError(
                "tool_sandbox_execution=container requires a working container runtime; "
                "refusing to downgrade to restricted_process"
            )
        # M15: 工具策略引擎与密钥提供（不可绕过；模型无法覆盖）。
        self.secrets = SecretProvider(store_cmd=settings.secret_store_cmd)
        self.policy_engine = ToolPolicyEngine(
            mode=settings.tool_sandbox_mode,
            default_network_mode=settings.tool_policy_network_default,
            available_execution_class=execution_class,
        )
        self.tools.set_policy(self.policy_engine, self.secrets)
        # M15 强制沙箱：restricted_process 工具必须经子进程执行器；出口
        # 一律走本地 EgressProxy（白名单 + DNS/IP + 重定向校验）。审计写入
        # sandbox_executions / egress_audit_events（失败静默降级）。
        async def _record_sandbox(record: dict[str, object]) -> None:
            try:
                await self.repository.record_sandbox_execution(record)
            except Exception:  # noqa: BLE001 - 审计失败不阻断执行
                pass

        async def _record_egress(record: dict[str, object]) -> None:
            try:
                await self.repository.record_egress_event(record)
            except Exception:  # noqa: BLE001
                pass

        self.egress_proxy = EgressProxy(
            allowed_hosts=self.tools.allowed_egress_hosts(),
            recorder=_record_egress,
            bind_host=settings.egress_proxy_bind_host,
        )
        def _sandbox_path(value: Path) -> str:
            if execution_class == "container":
                return str(value)
            return str(value.resolve())

        sandbox_crawler_env = {
            "COIFESP_DEMO_MODE": "1" if settings.demo_mode else "0",
            "COIFESP_MEDIACRAWLER_ROOT": _sandbox_path(settings.mediacrawler_root),
            "COIFESP_MEDIACRAWLER_OUTPUT_ROOT": _sandbox_path(
                settings.mediacrawler_output_root
            ),
            "COIFESP_MEDIACRAWLER_PYTHON_EXECUTABLE": str(
                settings.mediacrawler_python_executable
                or ("python3" if execution_class == "container" else sys.executable)
            ),
            "COIFESP_MEDIACRAWLER_ENTRYPOINT": _sandbox_path(
                settings.mediacrawler_entrypoint
            ),
            "COIFESP_MEDIACRAWLER_LOGIN_TYPE": settings.mediacrawler_login_type,
            "COIFESP_MEDIACRAWLER_HEADLESS": (
                "true" if settings.mediacrawler_headless else "false"
            ),
            "COIFESP_MEDIACRAWLER_INCLUDE_COMMENTS": (
                "1" if settings.mediacrawler_include_comments else "0"
            ),
            "COIFESP_MEDIACRAWLER_MAX_COMMENTS_PER_POST": str(
                settings.mediacrawler_max_comments_per_post
            ),
            "COIFESP_MEDIACRAWLER_TIMEOUT_SECONDS": str(
                int(settings.mediacrawler_timeout_seconds)
            ),
            "COIFESP_MEDIACRAWLER_MAX_OUTPUT_RUNS": str(
                settings.mediacrawler_max_output_runs
            ),
            "COIFESP_MEDIACRAWLER_USAGE_MODE": settings.mediacrawler_usage_mode,
        }
        self.sandbox_executor = SandboxedToolExecutor(
            recorder=_record_sandbox,
            executor=build_sandbox_executor(execution_class),
            base_env=sandbox_crawler_env,
        )
        self.tools.set_sandbox_executor(
            self.sandbox_executor,
            secrets=self.secrets,
            egress_proxy=self.egress_proxy,
        )

        # M16: 内容安全护栏已装配（见上方 ContentSecurityService 创建）。
        self.tools.set_security(self.content_security)
        # M9: MCP client manager over the configured allow-list. Servers
        # are connected lazily; discovery happens in start() so a failing
        # server never blocks the container from coming up.
        self.mcp = McpClientManager(settings.mcp_servers)
        # M5: Skill manifest 的工具与权限依赖预检（fail fast，避免清单
        # 静默引用不存在的工具或未知权限）。
        missing_tools = self.skills.validate_tools(self.tools.names())
        if missing_tools:
            raise ApplicationError(
                f"Skills reference unknown tools: {missing_tools}",
                code="skill_tool_dependency_missing",
            )
        unknown_permissions = self.skills.validate_permissions(_KNOWN_PERMISSIONS)
        if unknown_permissions:
            raise ApplicationError(
                f"Skills declare unknown permissions: {unknown_permissions}",
                code="skill_permission_unknown",
            )
        # M23: 记忆治理已在上方（工具注册前）装配。
        self.context_builder = ContextBuilder(
            self.repository,
            self.knowledge,
            settings,
            security=self.content_security,
        )
        self.summarizer = ConversationSummarizer(
            self.repository,
            self.knowledge,
            self.llm,
            settings,
            governance=self.memory_governance,
        )
        self.memory_service = MemoryExtractionService(
            self.knowledge,
            self.embeddings,
            governance=self.memory_governance,
        )
        self.memory_extractor = CaseMemoryExtractor(
            self.repository,
            self.knowledge,
            self.memory_service,
        )
        self.checkpointer: Any = None
        self._checkpointer_cm: Any = None
        # M21/M22: 一次性授权消费（审批 → 授权签发 → 业务同事务原子消费）。
        self.authorization = AuthorizationService(self.repository)
        self.worker = GraphWorker(
            self.repository,
            self.llm,
            self.tools,
            self.skills,
            worker_id=settings.worker_id,
            poll_interval_seconds=settings.worker_poll_interval_seconds,
            lease_seconds=settings.worker_lease_seconds,
            max_turns=settings.agent_max_turns,
            max_tool_calls=settings.agent_max_tool_calls,
            max_cost=settings.default_max_budget,
            checkpointer=None,
            context_builder=self.context_builder,
            summarizer=self.summarizer,
            extractor=self.memory_extractor,
            social=self.social,
            telemetry=self.telemetry,
            authorization=self.authorization,
        )
        self.agent_service = AgentRunService(
            self.repository,
            self.worker,
        )
        # M17: 显式目标、计划图与完成条件。
        self.goal_service = GoalService(self.repository)
        self.alignment_repository = AlignmentRepository(self.database)
        self.integrity_repository = IntegrityRepository(self.database)
        self.media_repository = MediaPipelineRepository(self.database)
        self.uncertainty_repository = UncertaintyRepository(self.database)
        self.media_fetch = MediaFetchService(settings.media_storage_root)
        self.media_worker = MediaPipelineWorker(
            self.media_repository,
            self.media_fetch,
            worker_id=settings.monitor_worker_id + "-media",
            poll_interval_seconds=settings.media_pipeline_poll_interval_seconds,
            lease_seconds=settings.media_pipeline_lease_seconds,
            enabled=settings.media_pipeline_enabled,
            probe_provider=FFprobeProvider(),
            ocr_provider=TesseractOCRProvider(),
            asr_provider=WhisperASRProvider(),
            frame_extractor=FFmpegFrameExtractor(),
            c2pa_verifier=C2PAToolProvider(),
            app_repository=self.repository,
            knowledge=self.knowledge,
        )
        self.media_capabilities = probe_capabilities()
        self.monitor_repository = MonitorRepository(self.database)
        self.monitor_scheduler = MonitorScheduler(
            self.monitor_repository,
            self.social,
            self.crawler,
            self.agent_service,
            worker_id=settings.monitor_worker_id,
            poll_interval_seconds=settings.monitor_poll_interval_seconds,
            lease_seconds=settings.monitor_lease_seconds,
            overlap_seconds=settings.monitor_overlap_seconds,
            enabled=settings.monitor_scheduler_enabled,
            max_concurrent_executions=settings.monitor_max_concurrent_executions,
        )
        self.alignment_service = AlignmentService(
            self.alignment_repository,
            self.media_repository,
            self.repository,
            self.social,
        )
        self.integrity_service = IntegrityService(
            self.integrity_repository,
            self.repository,
            self.social,
        )
        self.analysis_job_repository = AnalysisJobRepository(self.database)
        self.analysis_job_worker = AnalysisJobWorker(
            self.analysis_job_repository,
            alignment_service=self.alignment_service,
            integrity_service=self.integrity_service,
            worker_id=settings.monitor_worker_id + "-analysis",
        )
        self.debate_service = DebateService(
            self.repository,
            self.social,
            self.llm,
            profiles=PlatformProfileService(
                self.knowledge, self.llm, governance=self.memory_governance
            ),
        )
        self.review_service = ReviewService(self.repository)
        # M20: 评测运行与发布门禁。
        self.evaluation_service = EvaluationService(self.repository)
        # M22: 故障隔离、降级与事故处置。
        self.resilience_repository = ResilienceRepository(self.database)
        self.resilience = ResilienceService(
            self.resilience_repository,
            settings,
            telemetry=self.telemetry,
        )
        self.notification_service = NotificationService(
            self.repository,
            share_downloads_per_minute=settings.share_downloads_per_minute,
        )
        self.notify_dispatcher = NotificationDispatcher(
            self.repository,
            worker_id=settings.worker_id + "-notify",
            poll_interval_seconds=settings.event_poll_interval_seconds * 2,
            secret_resolver=self.secrets.resolve,
        )
        # M11: A2A gateway — local durable machinery by default; the remote
        # placeholder surfaces an explicit 501 when a URL is configured.
        if settings.a2a_remote_url:
            self.a2a_gateway = RemoteAgentGateway(settings.a2a_remote_url)
        else:
            self.a2a_gateway = LocalAgentGateway(self.repository, self.agent_service)
        self.graph = CaseAnalysisGraph(self.repository, self.tools)
        self.runner = AnalysisRunner(
            self.repository,
            self.graph,
            demo_mode=settings.demo_mode,
        )

    async def start(self) -> None:
        await self.database.create_schema()
        await self.egress_proxy.start()
        self.checkpointer, self._checkpointer_cm = await create_checkpointer(
            self.settings.database_url
        )
        # M9: discover allow-listed MCP servers and register their tools as
        # `mcp:{server}:{tool}`. Failures are logged and skipped per server.
        await register_mcp_tools(self.tools, self.mcp)
        self.worker.set_checkpointer(self.checkpointer)
        await self.worker.start()
        await self.worker.recover()
        await self.monitor_scheduler.start()
        await self.notify_dispatcher.start()
        await self.media_worker.start()
        await self.analysis_job_worker.start()
        # P0-1.5: CaseAnalysisGraph / AnalysisRunner are Legacy fixtures.
        # Production never recovers leftover analysis_tasks; those rows stay
        # pending and do not write Artifacts. Tests construct the runner
        # themselves when they need the old graph.

    async def stop(self) -> None:
        await self.worker.stop()
        await self.monitor_scheduler.stop()
        await self.notify_dispatcher.stop()
        await self.media_worker.stop()
        await self.analysis_job_worker.stop()
        await self.egress_proxy.stop()
        # Legacy runner is never started in production, so there is nothing
        # to cancel. Keep the instance for explicit test construction only.
        await self.mcp.close()
        if self._checkpointer_cm is not None:
            await self._checkpointer_cm.__aexit__(None, None, None)
        await self.database.dispose()

    @staticmethod
    def _build_crawler(settings: Settings) -> DemoCrawlerAdapter | MediaCrawlerAdapter:
        if settings.demo_mode:
            return DemoCrawlerAdapter()
        return MediaCrawlerAdapter(
            MediaCrawlerConfig(
                root=settings.mediacrawler_root.resolve(),
                output_root=settings.mediacrawler_output_root.resolve(),
                python_executable=(
                    settings.mediacrawler_python_executable or Path(sys.executable)
                ).resolve(),
                entrypoint=settings.mediacrawler_entrypoint.resolve(),
                login_type=settings.mediacrawler_login_type,
                headless=settings.mediacrawler_headless,
                include_comments=settings.mediacrawler_include_comments,
                max_comments_per_post=settings.mediacrawler_max_comments_per_post,
                timeout_seconds=settings.mediacrawler_timeout_seconds,
                max_output_runs=settings.mediacrawler_max_output_runs,
                usage_mode=settings.mediacrawler_usage_mode,
                weibo_cookies=settings.mediacrawler_weibo_cookies.get_secret_value(),
                bilibili_cookies=(
                    settings.mediacrawler_bilibili_cookies.get_secret_value()
                ),
                tieba_cookies=settings.mediacrawler_tieba_cookies.get_secret_value(),
                zhihu_cookies=settings.mediacrawler_zhihu_cookies.get_secret_value(),
                douyin_cookies=settings.mediacrawler_douyin_cookies.get_secret_value(),
            )
        )

    @staticmethod
    def _build_skills() -> SkillRegistry:
        """从磁盘加载八个 Skill 目录的 SKILL.md manifest（单一事实来源）。"""
        skill_root = Path(__file__).resolve().parents[1] / "skills"
        return SkillRegistry.scan(skill_root)
