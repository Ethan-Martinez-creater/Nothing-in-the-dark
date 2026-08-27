"""Tests for delete cascade across round-01 modules (A-01)."""

from __future__ import annotations

import atexit
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.application.repositories import ApplicationRepository
from app.infrastructure.database import Database
from app.infrastructure.database.alignment_repository import AlignmentRepository
from app.infrastructure.database.integrity_repository import IntegrityRepository
from app.infrastructure.database.media_pipeline_repository import MediaPipelineRepository
from app.infrastructure.database.models import (
    AlertOccurrenceRecord,
    AlertRuleRecord,
    AlignmentCandidateRecord,
    AlternativeHypothesisRecord,
    AnalysisAssumptionRecord,
    BehaviorFeatureSnapshotRecord,
    CanonicalEntityRecord,
    ConclusionConfidenceRecord,
    ContentFamilyMemberRecord,
    ContentFamilyRecord,
    CoordinationClusterRecord,
    CoordinationMemberRecord,
    EntityMentionRecord,
    MediaAssetRecord,
    MediaDerivativeRecord,
    MediaPipelineJobRecord,
    MediaTranscriptRecord,
    MonitorCursorRecord,
    MonitorDefinitionRecord,
    MonitorExecutionRecord,
    NarrativeMembershipRecord,
    QualityAssessmentRecord,
    RiskAssessmentRecord,
    SensitivityRunRecord,
)
from app.infrastructure.database.monitor_repository import MonitorRepository
from app.infrastructure.database.uncertainty_repository import UncertaintyRepository
from app.schemas.cases import CreateCaseRequest

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-del-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


async def _count(database: Database, model: type) -> int:
    async with database.session_factory() as session:
        return (await session.scalar(select(func.count()).select_from(model))) or 0


async def _seed_all(database: Database) -> str:
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(
        CreateCaseRequest(topic="删除级联", platforms=["weibo"])
    )
    case_id = case.id

    # 01 监测
    mon = MonitorRepository(database)
    monitor = await mon.create_monitor(
        case_id=case_id, name="m", interval_seconds=3600, platforms=["weibo"]
    )
    rule = await mon.create_rule(monitor_id=monitor.id, rule_type="absolute_volume")
    await mon.upsert_cursor(monitor_id=monitor.id, platform="weibo")
    await mon.create_execution(
        monitor_id=monitor.id, scheduled_at=datetime.now(UTC)
    )
    await mon.upsert_alert_occurrence(
        monitor_id=monitor.id, rule_id=rule.id, fingerprint="f", cooldown_bucket="b",
        severity="warning", explanation="e", metric_snapshot={}, evidence_refs={},
    )

    # 04 媒体
    media = MediaPipelineRepository(database)
    asset = await app_repo.create_media_asset(
        case_id=case_id, post_id=None, platform="weibo", media_type="image",
        url="https://example.com/x.png", normalized_url="https://example.com/x.png",
    )
    await media.create_job(asset.id, "download")
    await media.create_derivative(asset_id=asset.id, kind="ocr")
    await media.create_transcript(asset_id=asset.id, kind="asr")

    # 06 对齐
    align = AlignmentRepository(database)
    entity = await align.upsert_canonical_entity(
        case_id=case_id, entity_type="account", canonical_name="主体A"
    )
    await align.create_entity_mention(
        case_id=case_id, entity_id=entity.id, platform_object_type="post",
        platform_object_id="p1",
    )
    family = await align.create_content_family(case_id=case_id, label="内容族")
    await align.add_family_member(family_id=family.id, member_id="m1")
    await align.create_alignment_candidate(
        case_id=case_id, left_type="account", left_id="a", right_type="account",
        right_id="b", decision="possible",
    )

    # 07 完整性
    integ = IntegrityRepository(database)
    await integ.upsert_risk_assessment(
        case_id=case_id, subject_type="account", subject_id="a1",
        risk_type="automation", score=0.8, band="high",
    )
    await integ.create_cluster(case_id=case_id, size=2, score=0.5)

    # 08 不确定性（表保留）
    unc = UncertaintyRepository(database)
    await unc.upsert_quality_assessment(
        case_id=case_id, target_type="opinion", target_id="o1",
        dimension="coverage", level="low",
    )
    await unc.create_hypothesis(case_id=case_id, statement="替代解释")
    await unc.upsert_conclusion_confidence(
        case_id=case_id, conclusion_id="c1", final_level="low",
    )
    return case_id


async def test_delete_case_cleans_all_modules() -> None:
    database = Database(f"sqlite+aiosqlite:///{_tmp_db()}")
    await database.create_schema()
    case_id = await _seed_all(database)
    repo = ApplicationRepository(database)

    # 删除前各表应有记录。
    assert await _count(database, MonitorDefinitionRecord) == 1
    assert await _count(database, MediaAssetRecord) == 1
    assert await _count(database, CanonicalEntityRecord) == 1
    assert await _count(database, RiskAssessmentRecord) == 1
    assert await _count(database, QualityAssessmentRecord) == 1

    await repo.delete_case(case_id)

    # 删除后各模块表无孤儿记录。
    for model in (
        AlertOccurrenceRecord,
        AlertRuleRecord,
        MonitorCursorRecord,
        MonitorExecutionRecord,
        MonitorDefinitionRecord,
        MediaDerivativeRecord,
        MediaPipelineJobRecord,
        MediaTranscriptRecord,
        MediaAssetRecord,
        EntityMentionRecord,
        CanonicalEntityRecord,
        ContentFamilyMemberRecord,
        ContentFamilyRecord,
        AlignmentCandidateRecord,
        NarrativeMembershipRecord,
        BehaviorFeatureSnapshotRecord,
        RiskAssessmentRecord,
        CoordinationMemberRecord,
        CoordinationClusterRecord,
        QualityAssessmentRecord,
        AnalysisAssumptionRecord,
        SensitivityRunRecord,
        AlternativeHypothesisRecord,
        ConclusionConfidenceRecord,
    ):
        assert await _count(database, model) == 0, f"{model.__tablename__} not cleaned"


async def test_delete_monitor_cleans_children() -> None:
    database = Database(f"sqlite+aiosqlite:///{_tmp_db()}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="监测删除", platforms=["weibo"]))
    mon = MonitorRepository(database)
    monitor = await mon.create_monitor(
        case_id=case.id, name="m", interval_seconds=3600
    )
    rule = await mon.create_rule(monitor_id=monitor.id, rule_type="absolute_volume")
    await mon.upsert_cursor(monitor_id=monitor.id, platform="weibo")
    await mon.create_execution(monitor_id=monitor.id, scheduled_at=datetime.now(UTC))
    await mon.upsert_alert_occurrence(
        monitor_id=monitor.id, rule_id=rule.id, fingerprint="f", cooldown_bucket="b",
        severity="warning", explanation="e", metric_snapshot={}, evidence_refs={},
    )

    await mon.delete_monitor(monitor.id)

    assert await _count(database, MonitorDefinitionRecord) == 0
    assert await _count(database, MonitorCursorRecord) == 0
    assert await _count(database, MonitorExecutionRecord) == 0
    assert await _count(database, AlertRuleRecord) == 0
    assert await _count(database, AlertOccurrenceRecord) == 0
