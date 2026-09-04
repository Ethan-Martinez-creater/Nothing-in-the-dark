"""M6: Workspace Overview service — Home 聚合端点（count/limit 查询）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.infrastructure.database.engine import Database
from app.infrastructure.database.investigation_quality_repository import (
    InvestigationQualityRepository,
)
from app.infrastructure.database.models import (
    AgentRunRecord,
    AlertOccurrenceRecord,
    ApprovalRecord,
    ArtifactRecord,
    CaseRecord,
)
from app.schemas.workspace import (
    QualityAttentionCase,
    RecentInvestigation,
    RecentReport,
    TopSignal,
    WorkspaceCounts,
    WorkspaceOverviewResponse,
)

_ACTIVE_RUN_STATUSES = ("pending", "running", "waiting_approval")


class WorkspaceOverviewService:
    def __init__(
        self,
        database: Database,
        signal_service: Any,
        quality_repository: InvestigationQualityRepository,
    ) -> None:
        self._database = database
        self._signals = signal_service
        self._quality = quality_repository

    async def overview(self) -> WorkspaceOverviewResponse:
        async with self._database.session_factory() as session:
            investigations = await session.scalar(
                select(func.count()).select_from(CaseRecord)
            )
            open_signals = await session.scalar(
                select(func.count())
                .select_from(AlertOccurrenceRecord)
                .where(AlertOccurrenceRecord.status.in_(("open", "acknowledged")))
            )
            pending_approvals = await session.scalar(
                select(func.count())
                .select_from(ApprovalRecord)
                .where(ApprovalRecord.status == "pending")
            )
            running_runs = await session.scalar(
                select(func.count())
                .select_from(AgentRunRecord)
                .where(AgentRunRecord.status.in_(_ACTIVE_RUN_STATUSES))
            )
            recent_cases = (
                (
                    await session.execute(
                        select(CaseRecord)
                        .order_by(CaseRecord.updated_at.desc())
                        .limit(5)
                    )
                )
                .scalars()
                .all()
            )
            recent_reports = (
                (
                    await session.execute(
                        select(ArtifactRecord)
                        .where(ArtifactRecord.kind == "report")
                        .order_by(ArtifactRecord.created_at.desc())
                        .limit(5)
                    )
                )
                .scalars()
                .all()
            )

        top_signals = (await self._signals.list_signals(limit=5))[:5]

        # V3 §44：Home 只读持久化 Quality，不为所有 Case 同步 recompute；
        # 无 QualityRecord 的 Case 计入 quality_unassessed_count，避免把
        # 「尚未评估」错误显示成「质量正常」。
        attention_records = await self._quality.list_needing_attention(limit=5)
        attention_titles: dict[str, str] = {}
        if attention_records:
            async with self._database.session_factory() as session:
                rows = await session.execute(
                    select(CaseRecord.id, CaseRecord.title).where(
                        CaseRecord.id.in_([r.case_id for r in attention_records])
                    )
                )
                attention_titles = {row_id: title for row_id, title in rows.all()}
        unassessed_count = await self._quality.count_unassessed(
            int(investigations or 0)
        )

        return WorkspaceOverviewResponse(
            counts=WorkspaceCounts(
                investigations=int(investigations or 0),
                open_signals=int(open_signals or 0),
                pending_approvals=int(pending_approvals or 0),
                running_runs=int(running_runs or 0),
            ),
            recent_investigations=[
                RecentInvestigation(
                    id=case.id,
                    title=case.title,
                    topic=case.topic,
                    platforms=list(case.platforms or []),
                    status=str(case.status),
                    updated_at=case.updated_at.isoformat() if case.updated_at else "",
                )
                for case in recent_cases
            ],
            top_signals=[
                TopSignal(
                    id=signal.id,
                    signal_type=signal.signal_type,
                    severity=signal.severity,
                    status=signal.status,
                    title=signal.title,
                    why_it_matters=signal.why_it_matters,
                    case_id=signal.case_id,
                    case_title=signal.case_title,
                    detected_at=signal.detected_at.isoformat() if signal.detected_at else "",
                )
                for signal in top_signals
            ],
            recent_reports=[
                RecentReport(
                    artifact_id=artifact.id,
                    case_id=artifact.case_id,
                    title=artifact.title,
                    created_at=artifact.created_at.isoformat() if artifact.created_at else "",
                )
                for artifact in recent_reports
            ],
            investigations_needing_attention=[
                QualityAttentionCase(
                    case_id=record.case_id,
                    title=attention_titles.get(record.case_id, record.case_id),
                    grade=record.grade,
                    overall_score=record.overall_score,
                    computed_at=(
                        record.computed_at.isoformat() if record.computed_at else ""
                    ),
                )
                for record in attention_records
            ],
            quality_unassessed_count=int(unassessed_count),
        )
