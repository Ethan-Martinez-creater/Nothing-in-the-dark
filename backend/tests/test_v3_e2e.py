"""V3 §89-§101: Intelligence Depth E2E flow（确定性、无 LLM）。

真实 repository + 真实 service 全链路（seed → alignment/integrity →
intelligence_refresh → 断言）。覆盖计划 E2E-B/C/D/G/H/I/M 的数据面场景；
E2E-K/L（Copilot 对话路由）由浏览器 E2E（frontend/e2e-interact.cjs +
LLM 环境）覆盖；E2E-F retract 语义在 E04-E06 单测覆盖，此处验证链路。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.application.advanced_signal_service import AdvancedSignalDetectorService
from app.application.alignment_service import AlignmentService
from app.application.analysis_job_worker import AnalysisJobWorker
from app.application.collection_service import CollectionDefinitionService
from app.application.cross_investigation_service import CrossInvestigationService
from app.application.integrity_service import IntegrityService
from app.application.intelligence_refresh_service import IntelligenceRefreshService
from app.application.investigation_quality_service import (
    InvestigationQualityService,
)
from app.application.report_document_service import ReportDocumentService
from app.application.repositories import ApplicationRepository
from app.application.signal_service import SignalService
from app.application.workspace_entity_service import WorkspaceEntityService
from app.infrastructure.database.alignment_repository import AlignmentRepository
from app.infrastructure.database.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.database.collection_run_repository import (
    CollectionRunRepository,
)
from app.infrastructure.database.cross_investigation_repository import (
    CrossInvestigationRepository,
)
from app.infrastructure.database.derived_signal_repository import (
    DerivedSignalRepository,
)
from app.infrastructure.database.finding_repository import FindingRepository
from app.infrastructure.database.integrity_repository import IntegrityRepository
from app.infrastructure.database.investigation_quality_repository import (
    InvestigationQualityRepository,
)
from app.infrastructure.database.media_pipeline_repository import (
    MediaPipelineRepository,
)
from app.infrastructure.database.models import (
    FindingRecord,
    MediaAssetRecord,
    WorkspaceEntityCaseLinkRecord,
)
from app.infrastructure.database.monitor_repository import MonitorRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.database.workspace_entity_repository import (
    WorkspaceEntityRepository,
)
from app.schemas.cases import CreateCaseRequest
from tests.memory_db import MemoryDatabase


async def _setup() -> SimpleNamespace:
    database = MemoryDatabase()
    await database.create_schema()
    app = ApplicationRepository(database)
    social = SocialRepository(database)
    alignment_repo = AlignmentRepository(database)
    integrity_repo = IntegrityRepository(database)
    workspace_repo = WorkspaceEntityRepository(database)
    cross_repo = CrossInvestigationRepository(database)
    derived_repo = DerivedSignalRepository(database)
    media_repo = MediaPipelineRepository(database)
    jobs_repo = AnalysisJobRepository(database)
    quality_repo = InvestigationQualityRepository(database)
    finding_repo = FindingRepository(database)
    collection_runs = CollectionRunRepository(database)

    alignment = AlignmentService(alignment_repo, media_repo, app, social)
    integrity = IntegrityService(integrity_repo, app, social)
    workspace = WorkspaceEntityService(
        workspace_repository=workspace_repo,
        alignment_repository=alignment_repo,
        application_repository=app,
        social_repository=social,
        integrity_repository=integrity_repo,
        database=database,
    )
    cross = CrossInvestigationService(
        cross_repository=cross_repo,
        workspace_repository=workspace_repo,
        workspace_service=workspace,
        social_repository=social,
        media_repository=media_repo,
        application_repository=app,
        database=database,
    )
    definitions = CollectionDefinitionService(database, llm=None)
    report_service = ReportDocumentService(database)
    quality = InvestigationQualityService(
        repository=app,
        social_repository=social,
        collection_run_repository=collection_runs,
        finding_repository=finding_repo,
        quality_repository=quality_repo,
        report_document_service=report_service,
        collection_definition_service=definitions,
        database=database,
    )
    signals = AdvancedSignalDetectorService(
        derived_repository=derived_repo,
        integrity_repository=integrity_repo,
        analysis_job_repository=jobs_repo,
        workspace_service=workspace,
        cross_repository=cross_repo,
        media_repository=media_repo,
        application_repository=app,
    )
    signal_service = SignalService(
        database, MonitorRepository(database), derived_repository=derived_repo
    )
    refresh = IntelligenceRefreshService(
        analysis_job_repository=jobs_repo,
        quality_service=quality,
        workspace_entity_service=workspace,
        cross_investigation_service=cross,
        advanced_signal_service=signals,
    )
    return SimpleNamespace(
        db=database,
        app=app,
        social=social,
        alignment=alignment,
        integrity=integrity,
        workspace=workspace,
        cross=cross,
        quality=quality,
        signals=signals,
        signal_service=signal_service,
        refresh=refresh,
        alignment_repo=alignment_repo,
        integrity_repo=integrity_repo,
        workspace_repo=workspace_repo,
        cross_repo=cross_repo,
        derived_repo=derived_repo,
        media_repo=media_repo,
        jobs_repo=jobs_repo,
        quality_repo=quality_repo,
        finding_repo=finding_repo,
    )


async def _case(env: SimpleNamespace, topic: str) -> Any:
    return await env.app.create_case(
        CreateCaseRequest(topic=topic, platforms=["weibo"])
    )


async def _account(
    env: SimpleNamespace, case_id: str, platform: str, native_id: str, name: str
) -> Any:
    return await env.app.upsert_account(
        case_id=case_id,
        platform=platform,
        native_id=native_id,
        name=name,
        normalized_name=name.lower(),
    )


def _post(
    platform: str,
    native_id: str,
    author_id: str,
    *,
    content: str = "E2E 事件讨论",
) -> dict[str, Any]:
    return {
        "platform": platform,
        "native_id": native_id,
        "content_type": "post",
        "title": "",
        "content": content,
        "author": f"author-{author_id}",
        "published_at": "2026-08-15T10:00:00+08:00",
        "engagement": 5,
        "metrics": {"total": 5},
        "url": f"https://example.com/{platform}/{native_id}",
        "raw": {"id": native_id, "user_id": author_id},
        "comments": [],
    }


async def _refresh_full_chain(env: SimpleNamespace, case_id: str) -> None:
    """真实链路：alignment + integrity job → intelligence_refresh（§61 顺序）。"""
    await env.alignment.analyze_case(case_id)
    integrity_result = await env.integrity.analyze_case(case_id)
    await env.jobs_repo.create_job(
        case_id=case_id,
        job_type="alignment",
        idempotency_key=f"e2e-align:{case_id}",
    )
    await env.jobs_repo.create_job(
        case_id=case_id,
        job_type="integrity",
        idempotency_key=f"e2e-int:{case_id}",
    )
    await env.refresh.refresh_case(case_id)
    assert integrity_result["assessments"] >= 0  # 保持引用（analyze 已执行）


# ---------------------------------------------------------------------------
# E2E-B / E2E-C: Investigation Quality
# ---------------------------------------------------------------------------


async def test_e2e_b_quality_card_payload_complete() -> None:
    env = await _setup()
    case = await _case(env, "质量场景")
    await _account(env, case.id, "weibo", "q1", "质量账号")
    await env.social.persist_batch(
        case_id=case.id,
        posts=[_post("weibo", "q-p1", "q1", content="质量场景帖子")],
    )
    await _refresh_full_chain(env, case.id)

    payload = await env.quality.evaluate(case.id)
    # E2E-B：6 维度 + grade + score + top gaps + computed_at
    dimension_keys = {dim["key"] for dim in payload["dimensions"]}
    assert len(payload["dimensions"]) == 6
    assert {"collection_coverage", "evidence_coverage", "finding_support"} <= dimension_keys
    assert payload["grade"] in (
        "strong",
        "acceptable",
        "needs_attention",
        "weak",
        "insufficient_data",
    )
    assert payload["computed_at"] is not None
    assert payload["algorithm_version"] == "quality-1.0.0"
    assert payload["gaps"] is not None
    await env.db.dispose()


async def test_e2e_c_fingerprint_changes_on_evidence_link_mutation() -> None:
    env = await _setup()
    case = await _case(env, "指纹场景")
    await _refresh_full_chain(env, case.id)
    first = await env.quality.evaluate(case.id)

    # 造 claim + evidence + supports link
    run = await env.app.create_agent_run(
        case_id=case.id, turn_id=None, objective="e2e", metadata={}
    )
    claim = await env.app.create_claim(
        case_id=case.id, text="E2E 主张", created_by_run_id=run.id
    )
    evidence = await env.app.create_evidence(
        case_id=case.id,
        claim_id=claim.id,
        source_type="post",
        source_id="e2e-p1",
        stance="supports",
        excerpt="E2E 证据",
    )
    finding = await env.finding_repo.create(
        FindingRecord(
            case_id=case.id,
            kind="conclusion",
            title="E2E 结论",
            statement="结论",
            status="candidate",
        )
    )
    await env.finding_repo.add_evidence_link(
        finding.id, evidence.id, "supports"
    )

    second = await env.quality.evaluate(case.id)
    # E2E-C：fingerprint 变化 + finding_support 维度可用
    assert second["input_fingerprint"] != first["input_fingerprint"]
    support_dim = next(
        dim for dim in second["dimensions"] if dim["key"] == "finding_support"
    )
    assert support_dim["available"] is True
    await env.db.dispose()


# ---------------------------------------------------------------------------
# E2E-D / E2E-E: Identity determinism
# ---------------------------------------------------------------------------


async def test_e2e_d_same_actor_exact_identity() -> None:
    env = await _setup()
    case_a = await _case(env, "同账号A")
    case_b = await _case(env, "同账号B")
    await _account(env, case_a.id, "weibo", "123", "账号123")
    await _account(env, case_b.id, "weibo", "123", "账号123")
    # AccountRecord 全局唯一：case B 的出现来自 SourcePost 作者维度
    await env.social.persist_batch(
        case_id=case_b.id,
        posts=[_post("weibo", "b-1", "123", content="同账号在 case B 的帖子")],
    )

    await env.workspace.refresh_case(case_a.id)
    await env.workspace.refresh_case(case_b.id)
    await env.cross.refresh_case(case_a.id)

    # E2E-D：一个确定性 WorkspaceEntity 节点，case appearances=2
    entity = await env.workspace_repo.find_by_key("platform_account", "weibo:123")
    assert entity is not None
    links = await env.workspace_repo.list_case_links(entity.id)
    assert len(links) == 2

    # A-B: shared_actor observed
    pair_links = await env.cross_repo.list_between(case_a.id, case_b.id)
    actor_links = [link for link in pair_links if link.relation_type == "shared_actor"]
    assert len(actor_links) == 1
    assert actor_links[0].status == "observed"
    await env.db.dispose()


async def test_e2e_e_no_false_name_merge() -> None:
    env = await _setup()
    case_a = await _case(env, "同名A")
    case_b = await _case(env, "同名B")
    await _account(env, case_a.id, "weibo", "111", "张三")
    await _account(env, case_b.id, "weibo", "222", "张三")

    await env.workspace.refresh_case(case_a.id)
    await env.workspace.refresh_case(case_b.id)

    # E2E-E：相同 name 不同 native_id → 2 个独立节点
    entity_a = await env.workspace_repo.find_by_key("platform_account", "weibo:111")
    entity_b = await env.workspace_repo.find_by_key("platform_account", "weibo:222")
    assert entity_a is not None and entity_b is not None
    assert entity_a.id != entity_b.id
    assert entity_a.canonical_name == "张三"
    assert entity_b.canonical_name == "张三"
    await env.db.dispose()


# ---------------------------------------------------------------------------
# E2E-G: Media reuse（exact SHA → Signal；phash candidate 排除）
# ---------------------------------------------------------------------------


async def _asset(
    env: SimpleNamespace, case_id: str, *, sha256: str | None, phash: str | None
) -> None:
    async with env.db.session_factory() as session:
        session.add(
            MediaAssetRecord(
                case_id=case_id,
                post_id=None,
                platform="weibo",
                media_type="image",
                url=f"https://example.com/media/{sha256 or phash}-{case_id[:6]}",
                normalized_url=f"https://example.com/media/{sha256 or phash}",
                file_sha256=sha256,
                actual_sha256=sha256,
                phash=phash,
                ocr_text=None,
            )
        )
        await session.commit()


async def test_e2e_g_media_reuse_signal_and_phash_exclusion() -> None:
    env = await _setup()
    case_a = await _case(env, "媒体A")
    case_b = await _case(env, "媒体B")
    await _asset(env, case_a.id, sha256="sha-exact-1", phash=None)
    await _asset(env, case_b.id, sha256="sha-exact-1", phash=None)
    # phash 相似但不 exact：不得产生 media_reuse Signal
    await _asset(env, case_a.id, sha256=None, phash="phash-similar")

    summary = await env.signals.refresh_media_reuse()

    assert summary["upserted"] == 1  # 只有 exact sha 产生 signal
    signals = await env.derived_repo.list(signal_type="media_reuse")
    assert len(signals) == 1
    assert signals[0].source_id == "sha-exact-1"
    assert sorted(signals[0].related_case_ids_json) == sorted([case_a.id, case_b.id])
    await env.db.dispose()


# ---------------------------------------------------------------------------
# E2E-H: Coordination currentness（scope = 最新 succeeded integrity job）
# ---------------------------------------------------------------------------


async def test_e2e_h_coordination_uses_latest_job_only() -> None:
    env = await _setup()
    case = await _case(env, "协调场景")
    old_cluster = await env.integrity_repo.create_cluster(
        case_id=case.id,
        size=5,
        score=0.95,
        explanation="历史协调",
        algorithm_version="sparse-signals-1.1.0",
        window_start=datetime.now(UTC),
        window_end=datetime.now(UTC),
        members=[{"account_id": f"acc-{i}"} for i in range(5)],
    )
    # 旧 job：包含旧 cluster（已被取代）
    old_job = await env.jobs_repo.create_job(
        case_id=case.id,
        job_type="integrity",
        idempotency_key="e2e-h-old",
    )
    await env.jobs_repo.complete_job(
        old_job.id, "worker-e2e", {"cluster_ids": [old_cluster.id]}
    )
    # 最新 job：空 cluster_ids（条件消失）
    new_job = await env.jobs_repo.create_job(
        case_id=case.id,
        job_type="integrity",
        idempotency_key="e2e-h-new",
    )
    await env.jobs_repo.complete_job(new_job.id, "worker-e2e", {"cluster_ids": []})

    summary = await env.signals.refresh_coordination([case.id])

    assert summary["upserted"] == 0  # 最新 job 无 cluster → 不产生 signal
    assert summary["stale_deactivated"] == 0  # 无既有 signal 可失效
    signals = await env.derived_repo.list(signal_type="coordination_cluster")
    assert signals == []  # 历史 cluster 不得成为当前 Signal（E2E-H）
    await env.db.dispose()


# ---------------------------------------------------------------------------
# E2E-I: Signal condition clears → resolved；恢复 → open + occurrence+1
# ---------------------------------------------------------------------------


async def test_e2e_i_actor_recurrence_lifecycle() -> None:
    env = await _setup()
    case_a = await _case(env, "复现A")
    case_b = await _case(env, "复现B")
    case_c = await _case(env, "复现C")
    for case in (case_a, case_b, case_c):
        await _account(env, case.id, "weibo", "recur", "复现账号")
        await env.social.persist_batch(
            case_id=case.id,
            posts=[_post("weibo", f"{case.id}-r1", "recur", content="复现账号帖子")],
        )
        await env.workspace.refresh_case(case.id)

    first = await env.signals.refresh_actor_recurrence()
    assert first["upserted"] == 1
    signals = await env.derived_repo.list(signal_type="actor_recurrence")
    assert len(signals) == 1
    assert signals[0].detector_active is True
    assert signals[0].status == "open"
    assert signals[0].occurrence_count == 1

    # 移除一个 case appearance → count < 3 → inactive + resolved
    entity = await env.workspace_repo.find_by_key("platform_account", "weibo:recur")
    assert entity is not None
    async with env.db.session_factory() as session:
        from sqlalchemy import select as sa_select

        link = await session.scalar(
            sa_select(WorkspaceEntityCaseLinkRecord).where(
                WorkspaceEntityCaseLinkRecord.entity_id == entity.id,
                WorkspaceEntityCaseLinkRecord.case_id == case_c.id,
            )
        )
        if link is not None:
            await session.delete(link)
            await session.commit()

    second = await env.signals.refresh_actor_recurrence()
    assert second["stale_deactivated"] == 1
    after = (await env.derived_repo.list(signal_type="actor_recurrence"))[0]
    assert after.detector_active is False
    assert after.status == "resolved"

    # 恢复 → open + occurrence+1
    async with env.db.session_factory() as session:
        session.add(
            WorkspaceEntityCaseLinkRecord(
                entity_id=entity.id,
                case_id=case_c.id,
                source_type="account",
                source_id="e2e-recur",
                method="e2e",
            )
        )
        await session.commit()

    third = await env.signals.refresh_actor_recurrence()
    assert third["upserted"] == 1
    revived = (await env.derived_repo.list(signal_type="actor_recurrence"))[0]
    assert revived.detector_active is True
    assert revived.status == "open"
    assert revived.occurrence_count == 2
    await env.db.dispose()


# ---------------------------------------------------------------------------
# E2E-M: Case delete 全链路清理
# ---------------------------------------------------------------------------


async def test_e2e_m_delete_case_cleans_v3_data() -> None:
    env = await _setup()
    case_a = await _case(env, "删除A")
    case_b = await _case(env, "保留B")
    await _account(env, case_a.id, "weibo", "del-1", "删除账号")
    await _account(env, case_b.id, "weibo", "del-1", "删除账号")
    await _refresh_full_chain(env, case_a.id)
    await _refresh_full_chain(env, case_b.id)
    # 触发全局 detector（cross_case_overlap / actor_recurrence 数据面）
    await env.signals.refresh_actor_recurrence()

    before = await env.quality_repo.get(case_a.id)
    assert before is not None

    await env.app.delete_case(case_a.id)

    # quality 无残留；其它 case 数据保留
    assert await env.quality_repo.get(case_a.id) is None
    assert await env.quality_repo.get(case_b.id) is not None
    # cross links：涉及 case_a 的全部删除
    assert await env.cross_repo.list_workspace() == []
    # 实体链接清理后无孤儿 signal / relation
    signals = await env.derived_repo.list()
    for signal in signals:
        links = await env.derived_repo.list_case_links(signal.id)
        assert case_a.id not in links  # §67：不得残留 case appearance
    await env.db.dispose()


# ---------------------------------------------------------------------------
# E2E-N / E2E-O: Production Refresh → Advanced Signals + Signal evidence 面
# （V3 Approval Rework R1/R7）
# ---------------------------------------------------------------------------


async def test_e2e_n_production_refresh_enqueues_and_runs_global_detectors() -> None:
    """真实 worker 链路：intelligence_refresh 完成 → 自动 enqueue
    advanced_signal_refresh → worker 消费 → 三类 global detector 执行，
    共享 SHA 媒体产出 media_reuse Signal。"""
    env = await _setup()
    case_a = await _case(env, "E2E-N 案A")
    case_b = await _case(env, "E2E-N 案B")
    await _account(env, case_a.id, "weibo", "n1", "复现账号N")
    await env.social.persist_batch(
        case_id=case_b.id, posts=[_post("weibo", "nb1", "n1")]
    )
    for sha in ("ab01" * 16, "cd02" * 16):
        for case_id in (case_a.id, case_b.id):
            async with env.db.session_factory() as session:
                session.add(
                    MediaAssetRecord(
                        case_id=case_id,
                        platform="weibo",
                        media_type="image",
                        url=f"https://example.com/media/{sha}-{case_id[:4]}",
                        normalized_url=f"https://example.com/media/{sha}",
                        actual_sha256=sha,
                    )
                )
                await session.commit()

    worker = AnalysisJobWorker(
        env.jobs_repo,
        intelligence_service=env.refresh,
        advanced_signal_service=env.signals,
        enabled=False,
    )
    await env.refresh.enqueue(case_a.id, source_key=f"e2e-n:intel:{case_a.id}")
    # 第一次 tick：消费 intelligence_refresh → 成功后自动 enqueue advanced
    await worker.tick()
    advanced_jobs = await env.jobs_repo.list_jobs(
        case_a.id, job_type="advanced_signal_refresh"
    )
    assert len(advanced_jobs) == 1
    assert advanced_jobs[0].idempotency_key.startswith("v3:advanced:")
    # 第二次 tick：消费 advanced_signal_refresh → refresh_global
    await worker.tick()
    advanced_job = await env.jobs_repo.get_job(advanced_jobs[0].id)
    assert advanced_job.status == "succeeded"
    result = advanced_job.result_json
    assert set(result) == {"actor_recurrence", "media_reuse", "cross_case_overlap"}

    # media_reuse Signal 由 global detector 真实产出（每个共享 SHA 一条）
    signals = await env.derived_repo.list()
    media_signals = [s for s in signals if s.signal_type == "media_reuse"]
    assert len(media_signals) == 2
    for signal in media_signals:
        assert signal.severity == "warning"
        assert sorted(signal.related_case_ids_json) == sorted(
            [case_a.id, case_b.id]
        )
    await env.db.dispose()


async def test_e2e_o_signal_detail_exposes_sha256_evidence() -> None:
    """真实 detector 产出 media_reuse → SignalService 单条读取返回
    evidence_refs.items 且包含 sha256（Rework R7）。"""
    env = await _setup()
    case_a = await _case(env, "E2E-O 案A")
    case_b = await _case(env, "E2E-O 案B")
    sha = "ef03" * 16
    for case_id in (case_a.id, case_b.id):
        async with env.db.session_factory() as session:
            session.add(
                MediaAssetRecord(
                    case_id=case_id,
                    platform="weibo",
                    media_type="image",
                    url=f"https://example.com/media/{sha}-{case_id[:4]}",
                    normalized_url=f"https://example.com/media/{sha}",
                    actual_sha256=sha,
                )
            )
            await session.commit()
    await env.signals.refresh_media_reuse()

    signals = await env.signal_service.list_signals(
        source_type="derived", signal_type="media_reuse"
    )
    assert len(signals) == 1
    detail = await env.signal_service.get_signal(signals[0].id)
    items = detail.evidence_refs.get("items")
    assert isinstance(items, list) and items
    assert any(item.get("sha256") == sha for item in items)
    await env.db.dispose()
