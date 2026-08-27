from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer

router = APIRouter()


@router.get("/capabilities")
async def get_capabilities(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    platform_records = await container.social.list_platform_capabilities()
    platform_capabilities = {
        record.platform: {
            "status": record.status,
            "checks": record.checks,
            "last_error": record.last_error,
            "verified_at": (
                record.verified_at.isoformat() if record.verified_at else None
            ),
        }
        for record in platform_records
    }
    return {
        "version": container.settings.app_version,
        "environment": container.settings.app_env,
        "demo_mode": container.settings.demo_mode,
        "framework": "langgraph",
        "production_entry": "messages",
        "legacy_analysis": False,
        "durable_checkpointer": (
            "postgresql"
            if container.settings.database_url.startswith("postgresql")
            else "memory"
        ),
        "platforms": ["weibo", "bilibili", "tieba", "zhihu", "douyin"],
        "platform_capabilities": platform_capabilities,
        "crawler": {
            "mode": "demo" if container.settings.demo_mode else "mediacrawler",
            "time_filter_mode": "post_filter",
            "historical_completeness_guaranteed": False,
            "max_output_runs": container.settings.mediacrawler_max_output_runs,
            "usage_mode": container.settings.mediacrawler_usage_mode,
            "license": "NON-COMMERCIAL LEARNING LICENSE 1.1",
        },
        "llm_configured": container.llm.configured,
        "llm": {
            "provider": container.settings.llm_provider,
            "configured": container.llm.configured,
            "routes": {
                "fast": bool(container.settings.llm_fast_model),
                "reasoning": bool(
                    container.settings.llm_reasoning_model
                    or container.settings.llm_fast_model
                ),
                "report": bool(
                    container.settings.llm_report_model
                    or container.settings.llm_reasoning_model
                    or container.settings.llm_fast_model
                ),
            },
        },
        "agents": [
            "coordinator",
            "opinion",
            "propagation",
            "verification",
            "evidence_critic",
            "report",
            "citation_validator",
        ],
        "tools": container.tools.describe(),
        "skills": container.skills.describe(),
        "protocols": {
            "mcp": "client_dependencies_declared",
            "a2a": "data_contract_reserved",
        },
        "sandbox": {
            "policy_mode": container.policy_engine.mode,
            "container_supported": bool(
                container.settings.tool_sandbox_mode
            )
            and container.policy_engine.mode == "enforce",
        },
    }


@router.get("/skills")
async def list_skills(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """SkillRegistry 中已注册的全部技能清单（技能浏览面板数据源）。"""
    return {
        "skills": container.skills.describe(),
        "total": len(container.skills.describe()),
    }


@router.get("/tools/capabilities")
async def list_tool_sandbox_capabilities(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """M15: 工具沙箱能力清单（执行类 / 网络 / 秘密 / 资源 / 风险）。

    不返回任何 secret 值，只返回名称与引用信息。
    """
    capabilities: list[dict[str, object]] = []
    for spec in container.tools.describe():
        manifest = container.tools.manifest_for(
            container.tools.get(str(spec["name"]))
        )
        capabilities.append(
            {
                "name": spec["name"],
                "execution_class": manifest.execution_class,
                "network": manifest.network,
                "secrets": list(manifest.secrets),
                "resources": manifest.resources,
                "risk_level": manifest.risk_level,
                "side_effects": manifest.side_effects,
                "requires_approval": spec.get("requires_approval", False),
                "enabled": spec.get("enabled", True),
            }
        )
    return {
        "policy_mode": container.policy_engine.mode,
        "container_supported": __import__(
            "app.harness.sandbox", fromlist=["container_supported"]
        ).container_supported(),
        "tools": capabilities,
    }


@router.get("/sandbox/health")
async def sandbox_health(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """M15: 沙箱健康与审计摘要。"""
    from app.harness.sandbox import container_supported

    return {
        "policy_mode": container.policy_engine.mode,
        "container_supported": container_supported(),
        "restricted_executor": "restricted_process",
        "note": (
            "Windows 开发模式隔离能力弱于 Linux 容器；生产缺少强沙箱支持时"
            "高风险工具拒绝启动（fail closed）。"
        ),
    }


@router.get("/semantics/models")
async def list_semantic_models(
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    """M11: 语义模型/词典版本清单（首次访问登记规则基线）。"""
    from app.infrastructure.database.models import SemanticModelVersionRecord

    records = await container.repository.list_semantic_model_versions(limit=20)
    if not records:
        existing = await container.repository.list_semantic_model_versions(
            component="classifier", limit=1
        )
        if not existing:
            await container.repository.add_semantic_model_version(
                SemanticModelVersionRecord(
                    component="classifier",
                    version="semantics-rules-1.0.0",
                    capability="sentiment/stance/irony/claim_span/entity",
                    thresholds={"min_confidence": 0.35},
                )
            )
            records = await container.repository.list_semantic_model_versions(limit=20)
    return [
        {
            "id": r.id,
            "component": r.component,
            "version": r.version,
            "capability": r.capability,
            "training_data_version": r.training_data_version,
            "eval_data_version": r.eval_data_version,
            "thresholds": r.thresholds,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]



@router.get("/telemetry-health")
async def telemetry_health(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """M19: 可观测性健康检查——exporter 状态、span 覆盖与 SLO 快照。

    导出脱敏摘要；不包含原始内容、prompt 或配置 secret。
    """
    telemetry = getattr(container, "telemetry", None)
    if telemetry is None:
        return {"status": "noop", "reason": "telemetry not configured"}
    from app.telemetry.slo import evaluate_slos

    metrics = telemetry.metrics.snapshot()
    counters = metrics.get("counters", {})
    slo_results = evaluate_slos(
        api_total=int(counters.get("api.requests", 0)),
        api_ok=int(counters.get("api.requests", 0))
        - int(counters.get("api.errors", 0)),
        agent_total=int(counters.get("agent.runs", 0)),
        agent_ok=int(counters.get("agent.runs_ok", 0)),
    )
    return {
        "status": telemetry.health()["status"],
        "exporter": telemetry.health()["exporter"],
        "span_count": telemetry.health()["span_count"],
        "missing_attribute_count": telemetry.health()[
            "missing_attribute_count"
        ],
        "metrics_summary": {
            "api_requests": int(counters.get("api.requests", 0)),
            "api_errors": int(counters.get("api.errors", 0)),
            "agent_runs": int(counters.get("agent.runs", 0)),
            "agent_runs_ok": int(counters.get("agent.runs_ok", 0)),
            "llm_calls": int(counters.get("llm.calls", 0)),
            "llm_errors": int(counters.get("llm.errors", 0)),
            "latency_histograms": metrics.get("histograms", {}),
        },
        "slo": slo_results,
        "policy_version": "1.0",
    }
