from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.mcp.client import McpServerConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "COIFESP Agent API"
    app_version: str = "0.1.0"
    app_env: str = "development"
    app_debug: bool = True
    api_prefix: str = "/api/v1"

    database_url: str = "sqlite+aiosqlite:///./data/coifesp.db"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    llm_provider: str = "openai_compatible"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_fast_model: str = ""
    llm_reasoning_model: str = ""
    llm_report_model: str = ""
    llm_max_concurrency: int = 3
    llm_timeout_seconds: float = 120
    llm_max_retries: int = 3
    agent_max_turns: int = 16
    agent_max_tool_calls: int = 48
    agent_max_input_tokens: int = 120_000
    agent_max_output_tokens: int = 24_000

    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "auto"
    embedding_dimensions: int = 1024
    embedding_worker_url: str = ""
    embedding_timeout_seconds: float = 120
    rag_default_limit: int = 12
    context_token_budget: int = 4000
    context_history_turns: int = 10
    context_summary_max_tokens: int = 800

    demo_mode: bool = True
    artifact_root: Path = Path("../artifacts")
    mediacrawler_root: Path = Path("../vendor/MediaCrawler")
    mediacrawler_output_root: Path = Path("./data/crawls")
    mediacrawler_python_executable: Path | None = None
    mediacrawler_entrypoint: Path = Path("./scripts/mediacrawler_entry.py")
    mediacrawler_login_type: str = "qrcode"
    mediacrawler_headless: bool = False
    mediacrawler_include_comments: bool = True
    mediacrawler_max_comments_per_post: int = 10
    mediacrawler_timeout_seconds: float = 1800
    mediacrawler_max_output_runs: int = 100
    mediacrawler_usage_mode: str = "research"
    mediacrawler_weibo_cookies: SecretStr = SecretStr("")
    mediacrawler_bilibili_cookies: SecretStr = SecretStr("")
    mediacrawler_tieba_cookies: SecretStr = SecretStr("")
    mediacrawler_zhihu_cookies: SecretStr = SecretStr("")
    mediacrawler_douyin_cookies: SecretStr = SecretStr("")
    event_poll_interval_seconds: float = 0.35
    default_platforms: list[str] = Field(default_factory=lambda: ["weibo", "bilibili"])
    default_max_budget: float = 5.0

    worker_poll_interval_seconds: float = 1.0
    worker_lease_seconds: int = 300
    worker_id: str = "local-worker"

    # 01: 连续监测独立调度 Worker。
    monitor_scheduler_enabled: bool = True
    monitor_poll_interval_seconds: float = 5.0
    monitor_lease_seconds: int = 600
    monitor_overlap_seconds: int = 0
    monitor_max_concurrent_executions: int = 2
    monitor_worker_id: str = "local-monitor-worker"

    # 04: 多模态媒体流水线。
    media_storage_root: Path = Path("../artifacts/media")
    media_pipeline_enabled: bool = True
    media_pipeline_poll_interval_seconds: float = 2.0
    media_pipeline_lease_seconds: int = 600

    # M9: MCP server allow-list. Each entry names an external read-only MCP
    # server whose tools are discovered and registered as `mcp:{name}:{tool}`.
    # Only servers listed here can ever be contacted (whitelist).
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)

    # M11: A2A remote gateway placeholder. Remote A2A deployment is out of
    # the first delivery; if a URL is configured, the A2A routes still work
    # but task calls answer 501 (a2a_remote_not_deployed).
    a2a_remote_url: str | None = None

    # M15: 工具沙箱策略。audit_only 只记录不阻断，enforce 实际拒绝。
    tool_sandbox_mode: str = "enforce"
    # M15 执行类：restricted_process（开发）或 container（生产，需 docker）。
    # 部署环境设置 container 时自动启用真实容器执行器。
    tool_sandbox_execution: str = "restricted_process"
    # 容器模式 EgressProxy 需绑定非 loopback 地址供容器内 host.docker.internal 访问。
    egress_proxy_bind_host: str = "127.0.0.1"
    tool_policy_network_default: str = "none"
    secret_store_cmd: str | None = None

    # M16: 内容安全策略。enforce 隔离/阻断高风险注入；audit_only 只评估
    # 记录不阻断（用于红队测量与误报调优）。检测器不可用时策略层仍兜底。
    content_security_mode: str = "enforce"
    content_security_policy_version: str = "1.0"

    # M19: telemetry exporter（noop/console/in_memory/otlp_http）。
    telemetry_exporter: str = "noop"
    # otlp_http 生产端点（例：http://otel-collector:4318/v1/traces）。
    telemetry_otlp_endpoint: str = ""
    telemetry_otlp_service_name: str = "coifesp"

    # M23: 记忆治理写入策略版本（Gate 决策可追溯）。
    memory_governance_policy_version: str = "1.0"
    share_downloads_per_minute: int = 60

    # M22: 韧性——有界重试/熔断/背压/死信与事故处置。
    resilience_max_attempts: int = 3
    resilience_base_backoff_seconds: float = 1.0
    resilience_max_backoff_seconds: float = 30.0
    resilience_time_budget_seconds: float = 120.0
    resilience_queue_capacity: int = 200
    resilience_max_wait_seconds: float = 60.0
    resilience_db_watermark: float = 0.9
    resilience_disk_watermark: float = 0.95
    resilience_policy_version: str = "1.0"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
