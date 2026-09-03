from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select

from app.core.errors import ApplicationError, ResourceNotFoundError
from app.domain.enums import CaseStatus, EventType, TaskStatus
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    AcceptanceCriterionRecord,
    AccountRecord,
    AgentMessageRecord,
    AgentRunRecord,
    AlertOccurrenceRecord,
    AlertRuleRecord,
    AlignmentCandidateRecord,
    AlternativeHypothesisRecord,
    AnalysisAssumptionRecord,
    AnalysisTaskRecord,
    AnnotationCorrectionRecord,
    ApprovalRecord,
    ArtifactRecord,
    BehaviorFeatureSnapshotRecord,
    CanonicalEntityRecord,
    CaseActivityLogRecord,
    CaseRecord,
    ClaimRecord,
    CollectionDefinitionRecord,
    CompletionAssessmentRecord,
    ConclusionConfidenceRecord,
    ContentFamilyMemberRecord,
    ContentFamilyRecord,
    ContentSecurityAssessmentRecord,
    ConversationTurnRecord,
    CoordinationClusterRecord,
    CoordinationMemberRecord,
    CorrectionEventRecord,
    CorrectionImpactAnalysisRecord,
    CostSummaryRecord,
    DatasetExampleRecord,
    DatasetManifestRecord,
    DebateMessageRecord,
    DebateRecord,
    DebateVoteRecord,
    DeliveryAttemptRecord,
    EgressAuditEventRecord,
    EmbeddingVersionRecord,
    EntityMentionRecord,
    EntityRecord,
    EvaluationGateResultRecord,
    EvaluationRecord,
    EvaluationRunRecord,
    EvidenceRecord,
    ExecutionAuthorizationRecord,
    ExportJobRecord,
    FindingEvidenceLinkRecord,
    FindingRecord,
    FindingSourceLinkRecord,
    GoalRecord,
    GuardrailDecisionRecord,
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    LexiconEntryRecord,
    LifecycleSnapshotRecord,
    MediaAssetRecord,
    MediaDerivativeRecord,
    MediaPipelineJobRecord,
    MediaTranscriptRecord,
    MemoryRecord,
    ModelCallRecord,
    MonitorCursorRecord,
    MonitorDefinitionRecord,
    MonitorExecutionRecord,
    NarrativeClaimRecord,
    NarrativeMembershipRecord,
    NarrativePostRecord,
    NarrativeRecord,
    NarrativeTransitionRecord,
    NarrativeVersionRecord,
    NotificationEndpointRecord,
    NotificationEventRecord,
    PlanEdgeRecord,
    PlanStepRecord,
    PlanVersionRecord,
    ProjectRecord,
    PropagationEdgeRecord,
    PropagationNodeRecord,
    QualityAssessmentRecord,
    RawSocialRecord,
    ReleaseGateRecord,
    ReportDocumentRecord,
    ReviewAssignmentRecord,
    ReviewCommentRecord,
    ReviewDecisionRecord,
    ReviewItemRecord,
    ReviewPolicyRecord,
    RiskAssessmentRecord,
    RunEventRecord,
    RunSteeringRecord,
    SandboxExecutionRecord,
    SemanticAnnotationRecord,
    SemanticModelVersionRecord,
    SensitivityRunRecord,
    ShareLinkRecord,
    SourceCommentRecord,
    SourcePostRecord,
    StepEvidenceRecord,
    SubscriptionRecord,
    TaskEventRecord,
    ToolCallRecord,
)
from app.schemas.cases import CreateCaseRequest
from app.schemas.tasks import StartAnalysisRequest


class ApplicationRepository:
    """Persistence facade used by API services and background workflows."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_case(self, request: CreateCaseRequest) -> CaseRecord:
        record = CaseRecord(
            title=request.title or request.topic[:80],
            topic=request.topic,
            description=request.description,
            status=CaseStatus.READY,
            platforms=request.platforms,
            time_range={"start": request.time_start, "end": request.time_end},
            project_id=request.project_id,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    # ---------------- 项目（Project） ----------------

    async def create_project(self, title: str) -> ProjectRecord:
        record = ProjectRecord(title=title)
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_projects(self) -> Sequence[ProjectRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(ProjectRecord).order_by(ProjectRecord.created_at.desc())
            )
            return result.all()

    async def get_project(self, project_id: str) -> ProjectRecord:
        async with self._database.session_factory() as session:
            record = await session.get(ProjectRecord, project_id)
            if record is None:
                raise ResourceNotFoundError("project", project_id)
            return record

    async def rename_project(self, project_id: str, title: str) -> ProjectRecord:
        async with self._database.session_factory() as session:
            record = await session.get(ProjectRecord, project_id)
            if record is None:
                raise ResourceNotFoundError("project", project_id)
            record.title = title
            await session.commit()
            await session.refresh(record)
            return record

    async def delete_project(self, project_id: str) -> None:
        """Delete the project and every conversation (case) it contains."""
        await self.get_project(project_id)
        async with self._database.session_factory() as session:
            case_ids = (
                await session.scalars(
                    select(CaseRecord.id).where(
                        CaseRecord.project_id == project_id
                    )
                )
            ).all()
        # 先完整清理并删除每个 case（delete_case 做显式级联），
        # 再删 project（PG 外键方向 case→project，顺序不可反）。
        for case_id in case_ids:
            await self.delete_case(case_id)
        async with self._database.session_factory() as session:
            project = await session.get(ProjectRecord, project_id)
            if project is not None:
                await session.delete(project)
            await session.commit()

    async def list_cases(self) -> Sequence[CaseRecord]:
        async with self._database.session_factory() as session:
            # 按最近活跃排序：对话（add_turn）会 touch updated_at。
            query = select(CaseRecord).order_by(CaseRecord.updated_at.desc())
            result = await session.scalars(query)
            return result.all()

    async def get_case(self, case_id: str) -> CaseRecord:
        async with self._database.session_factory() as session:
            record = await session.get(CaseRecord, case_id)
            if record is None:
                raise ResourceNotFoundError("case", case_id)
            return record

    async def get_case_database_counts(self, case_id: str) -> dict[str, int]:
        """DB01: exact case-scoped counts of claim/evidence/artifact/review.

        ``review_decisions`` 无 case_id 列，必须 JOIN ReviewItem 并以
        ReviewItem.case_id 限定 Case scope（DB-INV-3），否则会泄漏其它
        Case 的 Review 数据。posts/comments/findings/reports/collection_runs
        不在此统计——它们由各自 Repository 负责。
        """
        async with self._database.session_factory() as session:
            claims = await session.scalar(
                select(func.count(ClaimRecord.id)).where(
                    ClaimRecord.case_id == case_id
                )
            )
            evidence = await session.scalar(
                select(func.count(EvidenceRecord.id)).where(
                    EvidenceRecord.case_id == case_id
                )
            )
            artifacts = await session.scalar(
                select(func.count(ArtifactRecord.id)).where(
                    ArtifactRecord.case_id == case_id
                )
            )
            review_items = await session.scalar(
                select(func.count(ReviewItemRecord.id)).where(
                    ReviewItemRecord.case_id == case_id
                )
            )
            review_decisions = await session.scalar(
                select(func.count(ReviewDecisionRecord.id))
                .join(
                    ReviewItemRecord,
                    ReviewDecisionRecord.item_id == ReviewItemRecord.id,
                )
                .where(ReviewItemRecord.case_id == case_id)
            )
        return {
            "claims": int(claims or 0),
            "evidence": int(evidence or 0),
            "artifacts": int(artifacts or 0),
            "review_items": int(review_items or 0),
            "review_decisions": int(review_decisions or 0),
        }

    async def set_case_status(self, case_id: str, status: CaseStatus) -> None:
        async with self._database.session_factory() as session:
            record = await session.get(CaseRecord, case_id)
            if record is None:
                raise ResourceNotFoundError("case", case_id)
            record.status = status
            await session.commit()

    async def rename_case(self, case_id: str, title: str) -> CaseRecord:
        async with self._database.session_factory() as session:
            record = await session.get(CaseRecord, case_id)
            if record is None:
                raise ResourceNotFoundError("case", case_id)
            record.title = title
            await session.commit()
            await session.refresh(record)
            return record

    async def update_case(
        self,
        case_id: str,
        *,
        title: str | None = None,
        topic: str | None = None,
        description: str | None = None,
        platforms: list[str] | None = None,
        time_start: str | None = None,
        time_end: str | None = None,
    ) -> CaseRecord:
        """PATCH 部分更新调查元数据；None 字段保持原值。

        platforms 由 UpdateCaseRequest validator 校验过；time_start/time_end
        只更新显式提供的端点（None 不覆盖已存在的另一端）。
        """
        async with self._database.session_factory() as session:
            record = await session.get(CaseRecord, case_id)
            if record is None:
                raise ResourceNotFoundError("case", case_id)
            if title is not None:
                record.title = title
            if topic is not None:
                record.topic = topic
            if description is not None:
                record.description = description
            if platforms is not None:
                record.platforms = list(platforms)
            if time_start is not None or time_end is not None:
                tr = dict(record.time_range or {})
                if time_start is not None:
                    tr["start"] = time_start
                if time_end is not None:
                    tr["end"] = time_end
                record.time_range = tr
            await session.commit()
            await session.refresh(record)
            return record

    async def delete_case(self, case_id: str) -> None:
        """Delete a case with explicit cascade cleanup.

        PostgreSQL 的表间没有 ORM 级联（CaseRecord 只对 turns/tasks/
        artifacts 声明了 delete-orphan），这里按 FK 依赖顺序显式删除
        全部关联行，最后删 case 本身。
        """
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            run_ids = (
                await session.scalars(
                    select(AgentRunRecord.id).where(
                        AgentRunRecord.case_id == case_id
                    )
                )
            ).all()
            task_ids = (
                await session.scalars(
                    select(AnalysisTaskRecord.id).where(
                        AnalysisTaskRecord.case_id == case_id
                    )
                )
            ).all()
            doc_ids = (
                await session.scalars(
                    select(KnowledgeDocumentRecord.id).where(
                        KnowledgeDocumentRecord.case_id == case_id
                    )
                )
            ).all()
            post_ids = (
                await session.scalars(
                    select(SourcePostRecord.id).where(
                        SourcePostRecord.case_id == case_id
                    )
                )
            ).all()

            # run 关联表（agent_runs 的子表）
            if run_ids:
                await session.execute(
                    delete(AgentMessageRecord).where(
                        or_(
                            AgentMessageRecord.sender_run_id.in_(run_ids),
                            AgentMessageRecord.receiver_run_id.in_(run_ids),
                        )
                    )
                )
                for model in (
                    RunSteeringRecord,
                    ModelCallRecord,
                    RunEventRecord,
                    ToolCallRecord,
                    ApprovalRecord,
                ):
                    await session.execute(
                        delete(model).where(model.run_id.in_(run_ids))
                    )
            # task 关联表
            if task_ids:
                await session.execute(
                    delete(TaskEventRecord).where(
                        TaskEventRecord.task_id.in_(task_ids)
                    )
                )
            # 领域表（先子后父）
            await session.execute(
                delete(NarrativeMembershipRecord).where(
                    NarrativeMembershipRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(EvidenceRecord).where(EvidenceRecord.case_id == case_id)
            )
            await session.execute(
                delete(ClaimRecord).where(ClaimRecord.case_id == case_id)
            )
            await session.execute(
                delete(PropagationEdgeRecord).where(
                    PropagationEdgeRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(PropagationNodeRecord).where(
                    PropagationNodeRecord.case_id == case_id
                )
            )
            # 知识资料
            if doc_ids:
                await session.execute(
                    delete(KnowledgeChunkRecord).where(
                        KnowledgeChunkRecord.document_id.in_(doc_ids)
                    )
                )
            await session.execute(
                delete(KnowledgeDocumentRecord).where(
                    KnowledgeDocumentRecord.case_id == case_id
                )
            )
            # 社交数据（comments 挂在 posts 下）
            if post_ids:
                await session.execute(
                    delete(SourceCommentRecord).where(
                        SourceCommentRecord.post_id.in_(post_ids)
                    )
                )
            await session.execute(
                delete(SourcePostRecord).where(SourcePostRecord.case_id == case_id)
            )
            await session.execute(
                delete(RawSocialRecord).where(RawSocialRecord.case_id == case_id)
            )
            # 其余 case 域表
            # M3/M4 新增产品层表：Finding links（无 case_id，经 findings 中转）
            # → findings → collection_definitions，均在 artifacts 之前删除。
            finding_ids = (
                await session.scalars(
                    select(FindingRecord.id).where(FindingRecord.case_id == case_id)
                )
            ).all()
            if finding_ids:
                await session.execute(
                    delete(FindingEvidenceLinkRecord).where(
                        FindingEvidenceLinkRecord.finding_id.in_(finding_ids)
                    )
                )
                await session.execute(
                    delete(FindingSourceLinkRecord).where(
                        FindingSourceLinkRecord.finding_id.in_(finding_ids)
                    )
                )
            await session.execute(
                delete(FindingRecord).where(FindingRecord.case_id == case_id)
            )
            await session.execute(
                delete(CollectionDefinitionRecord).where(
                    CollectionDefinitionRecord.case_id == case_id
                )
            )
            # M7：report_documents 必须先于 artifacts 删除（FK 依赖）。
            await session.execute(
                delete(ReportDocumentRecord).where(
                    ReportDocumentRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(ArtifactRecord).where(ArtifactRecord.case_id == case_id)
            )
            await session.execute(
                delete(AnalysisTaskRecord).where(
                    AnalysisTaskRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(MemoryRecord).where(MemoryRecord.case_id == case_id)
            )
            await session.execute(
                delete(AccountRecord).where(AccountRecord.case_id == case_id)
            )
            # 04 媒体派生表（先删子表再删 media_assets）
            media_ids = (
                await session.scalars(
                    select(MediaAssetRecord.id).where(
                        MediaAssetRecord.case_id == case_id
                    )
                )
            ).all()
            if media_ids:
                for model in (
                    MediaPipelineJobRecord,
                    MediaDerivativeRecord,
                    MediaTranscriptRecord,
                ):
                    await session.execute(
                        delete(model).where(model.asset_id.in_(media_ids))
                    )
            await session.execute(
                delete(MediaAssetRecord).where(MediaAssetRecord.case_id == case_id)
            )
            # 01 监测（先子后父）
            monitor_ids = (
                await session.scalars(
                    select(MonitorDefinitionRecord.id).where(
                        MonitorDefinitionRecord.case_id == case_id
                    )
                )
            ).all()
            if monitor_ids:
                await session.execute(
                    delete(AlertOccurrenceRecord).where(
                        AlertOccurrenceRecord.monitor_id.in_(monitor_ids)
                    )
                )
                await session.execute(
                    delete(AlertRuleRecord).where(
                        AlertRuleRecord.monitor_id.in_(monitor_ids)
                    )
                )
                await session.execute(
                    delete(MonitorCursorRecord).where(
                        MonitorCursorRecord.monitor_id.in_(monitor_ids)
                    )
                )
                await session.execute(
                    delete(MonitorExecutionRecord).where(
                        MonitorExecutionRecord.monitor_id.in_(monitor_ids)
                    )
                )
            await session.execute(
                delete(MonitorDefinitionRecord).where(
                    MonitorDefinitionRecord.case_id == case_id
                )
            )
            # 06 对齐（先子后父）
            entity_ids = (
                await session.scalars(
                    select(CanonicalEntityRecord.id).where(
                        CanonicalEntityRecord.case_id == case_id
                    )
                )
            ).all()
            if entity_ids:
                await session.execute(
                    delete(EntityMentionRecord).where(
                        EntityMentionRecord.entity_id.in_(entity_ids)
                    )
                )
            await session.execute(
                delete(CanonicalEntityRecord).where(
                    CanonicalEntityRecord.case_id == case_id
                )
            )
            family_ids = (
                await session.scalars(
                    select(ContentFamilyRecord.id).where(
                        ContentFamilyRecord.case_id == case_id
                    )
                )
            ).all()
            if family_ids:
                await session.execute(
                    delete(ContentFamilyMemberRecord).where(
                        ContentFamilyMemberRecord.family_id.in_(family_ids)
                    )
                )
            await session.execute(
                delete(ContentFamilyRecord).where(
                    ContentFamilyRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(AlignmentCandidateRecord).where(
                    AlignmentCandidateRecord.case_id == case_id
                )
            )
            # 07 完整性（先子后父）
            cluster_ids = (
                await session.scalars(
                    select(CoordinationClusterRecord.id).where(
                        CoordinationClusterRecord.case_id == case_id
                    )
                )
            ).all()
            if cluster_ids:
                await session.execute(
                    delete(CoordinationMemberRecord).where(
                        CoordinationMemberRecord.cluster_id.in_(cluster_ids)
                    )
                )
            await session.execute(
                delete(CoordinationClusterRecord).where(
                    CoordinationClusterRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(RiskAssessmentRecord).where(
                    RiskAssessmentRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(BehaviorFeatureSnapshotRecord).where(
                    BehaviorFeatureSnapshotRecord.case_id == case_id
                )
            )
            # 08 不确定性（表保留，删除案件时清理）
            await session.execute(
                delete(QualityAssessmentRecord).where(
                    QualityAssessmentRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(AnalysisAssumptionRecord).where(
                    AnalysisAssumptionRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(SensitivityRunRecord).where(
                    SensitivityRunRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(AlternativeHypothesisRecord).where(
                    AlternativeHypothesisRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(ConclusionConfidenceRecord).where(
                    ConclusionConfidenceRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(EntityRecord).where(EntityRecord.case_id == case_id)
            )
            await session.execute(
                delete(EvaluationRecord).where(
                    EvaluationRecord.case_id == case_id
                )
            )
            await session.execute(
                delete(CostSummaryRecord).where(
                    CostSummaryRecord.case_id == case_id
                )
            )
            # 辩论
            debate_ids = (
                await session.scalars(
                    select(DebateRecord.id).where(DebateRecord.case_id == case_id)
                )
            ).all()
            if debate_ids:
                await session.execute(
                    delete(DebateMessageRecord).where(
                        DebateMessageRecord.debate_id.in_(debate_ids)
                    )
                )
                await session.execute(
                    delete(DebateVoteRecord).where(
                        DebateVoteRecord.debate_id.in_(debate_ids)
                    )
                )
                await session.execute(
                    delete(DebateRecord).where(DebateRecord.case_id == case_id)
                )
            # run 本身最后删（claims/artifacts 等已先清理）
            if run_ids:
                await session.execute(
                    delete(AgentRunRecord).where(AgentRunRecord.case_id == case_id)
                )
            # conversation_turns 必须在 agent_runs 之后删：agent_runs.turn_id
            # 外键引用 turns（PG 强制），SQLite 测试未开 PRAGMA 外键所以
            # 之前的顺序错误只在 PG 上暴露（2026-08-10 冒烟发现）。
            await session.execute(
                delete(ConversationTurnRecord).where(
                    ConversationTurnRecord.case_id == case_id
                )
            )
            case = await session.get(CaseRecord, case_id)
            if case is not None:
                await session.delete(case)
            await session.commit()

    async def add_turn(
        self,
        case_id: str,
        *,
        role: str,
        content: str,
    ) -> ConversationTurnRecord:
        await self.get_case(case_id)
        record = ConversationTurnRecord(case_id=case_id, role=role, content=content)
        async with self._database.session_factory() as session:
            session.add(record)
            # touch updated_at：会话列表按最近活跃排序依赖它。
            case = await session.get(CaseRecord, case_id)
            if case is not None:
                case.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_turns(self, case_id: str) -> Sequence[ConversationTurnRecord]:
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(ConversationTurnRecord)
                .where(ConversationTurnRecord.case_id == case_id)
                .order_by(ConversationTurnRecord.created_at.asc())
            )
            return result.all()

    # ---------------- 辩论（Debate） ----------------

    async def create_debate(
        self,
        case_id: str,
        *,
        title: str,
        platform_roles: list[str],
    ) -> DebateRecord:
        await self.get_case(case_id)
        record = DebateRecord(
            case_id=case_id,
            title=title,
            platform_roles={"platforms": platform_roles},
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_debates(self, case_id: str) -> Sequence[DebateRecord]:
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(DebateRecord)
                .where(DebateRecord.case_id == case_id)
                .order_by(DebateRecord.created_at.desc())
            )
            return result.all()

    async def get_debate(self, debate_id: str) -> DebateRecord:
        async with self._database.session_factory() as session:
            record = await session.get(DebateRecord, debate_id)
            if record is None:
                raise ResourceNotFoundError("debate", debate_id)
            return record

    async def update_debate(
        self,
        debate_id: str,
        *,
        status: str | None = None,
        round: int | None = None,
    ) -> DebateRecord:
        async with self._database.session_factory() as session:
            record = await session.get(DebateRecord, debate_id)
            if record is None:
                raise ResourceNotFoundError("debate", debate_id)
            if status is not None:
                record.status = status
            if round is not None:
                record.round = round
            await session.commit()
            await session.refresh(record)
            return record

    async def add_debate_message(
        self,
        debate_id: str,
        *,
        role: str,
        round: int,
        content: str,
        platform: str | None = None,
    ) -> DebateMessageRecord:
        record = DebateMessageRecord(
            debate_id=debate_id,
            role=role,
            platform=platform,
            round=round,
            content=content,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_debate_messages(
        self, debate_id: str
    ) -> Sequence[DebateMessageRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(DebateMessageRecord)
                .where(DebateMessageRecord.debate_id == debate_id)
                .order_by(DebateMessageRecord.created_at.asc())
            )
            return result.all()

    async def has_debate_round_roles(
        self, debate_id: str, round: int
    ) -> bool:
        """当前轮是否已有平台角色发言（advance 幂等保护）。"""
        async with self._database.session_factory() as session:
            count = await session.scalar(
                select(DebateMessageRecord.id)
                .where(
                    DebateMessageRecord.debate_id == debate_id,
                    DebateMessageRecord.round == round,
                    DebateMessageRecord.role == "platform_role",
                )
                .limit(1)
            )
            return count is not None

    async def add_debate_vote(
        self,
        debate_id: str,
        *,
        platform: str,
        choice: str,
        reason: str,
    ) -> DebateVoteRecord:
        record = DebateVoteRecord(
            debate_id=debate_id,
            platform=platform,
            choice=choice,
            reason=reason,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_debate_votes(
        self, debate_id: str
    ) -> Sequence[DebateVoteRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(DebateVoteRecord)
                .where(DebateVoteRecord.debate_id == debate_id)
                .order_by(DebateVoteRecord.created_at.asc())
            )
            return result.all()

    async def create_task(
        self,
        case_id: str,
        request: StartAnalysisRequest,
    ) -> AnalysisTaskRecord:
        await self.get_case(case_id)
        record = AnalysisTaskRecord(
            case_id=case_id,
            options=request.model_dump(),
            status=TaskStatus.PENDING,
            current_stage="queued",
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_task(self, task_id: str) -> AnalysisTaskRecord:
        async with self._database.session_factory() as session:
            record = await session.get(AnalysisTaskRecord, task_id)
            if record is None:
                raise ResourceNotFoundError("task", task_id)
            return record

    async def list_case_tasks(self, case_id: str) -> Sequence[AnalysisTaskRecord]:
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(AnalysisTaskRecord)
                .where(AnalysisTaskRecord.case_id == case_id)
                .order_by(AnalysisTaskRecord.created_at.desc())
            )
            return result.all()

    async def list_recoverable_tasks(self) -> Sequence[AnalysisTaskRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(AnalysisTaskRecord).where(
                    AnalysisTaskRecord.status.in_(
                        [TaskStatus.PENDING, TaskStatus.RUNNING]
                    )
                )
            )
            return result.all()

    async def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        current_stage: str | None = None,
        progress: float | None = None,
        error: str | None = None,
    ) -> AnalysisTaskRecord:
        async with self._database.session_factory() as session:
            record = await session.get(AnalysisTaskRecord, task_id)
            if record is None:
                raise ResourceNotFoundError("task", task_id)
            if status is not None:
                record.status = status
            if current_stage is not None:
                record.current_stage = current_stage
            if progress is not None:
                record.progress = progress
            record.error = error
            await session.commit()
            await session.refresh(record)
            return record

    async def add_event(
        self,
        task_id: str,
        *,
        event_type: EventType,
        stage: str,
        message: str,
        progress: float,
        payload: dict[str, object] | None = None,
    ) -> TaskEventRecord:
        record = TaskEventRecord(
            task_id=task_id,
            event_type=event_type,
            stage=stage,
            message=message,
            progress=progress,
            payload=payload or {},
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_events(
        self,
        task_id: str,
        *,
        after_id: int = 0,
    ) -> Sequence[TaskEventRecord]:
        await self.get_task(task_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(TaskEventRecord)
                .where(
                    TaskEventRecord.task_id == task_id,
                    TaskEventRecord.id > after_id,
                )
                .order_by(TaskEventRecord.id.asc())
            )
            return result.all()

    async def create_artifact(
        self,
        *,
        case_id: str,
        kind: str,
        title: str,
        data: dict[str, object],
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> ArtifactRecord:
        async with self._database.session_factory() as session:
            latest_version = await session.scalar(
                select(ArtifactRecord.version)
                .where(
                    ArtifactRecord.case_id == case_id,
                    ArtifactRecord.kind == kind,
                )
                .order_by(ArtifactRecord.version.desc())
                .limit(1)
            )
            record = ArtifactRecord(
                case_id=case_id,
                task_id=task_id,
                run_id=run_id,
                kind=kind,
                title=title,
                version=(latest_version or 0) + 1,
                data=data,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_artifacts(self, case_id: str) -> Sequence[ArtifactRecord]:
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(ArtifactRecord)
                .where(ArtifactRecord.case_id == case_id)
                .order_by(ArtifactRecord.created_at.desc())
            )
            return result.all()

    async def get_artifact(self, artifact_id: str) -> ArtifactRecord:
        async with self._database.session_factory() as session:
            record = await session.get(ArtifactRecord, artifact_id)
            if record is None:
                raise ResourceNotFoundError("artifact", artifact_id)
            return record

    async def list_artifact_versions(
        self, artifact_id: str
    ) -> Sequence[ArtifactRecord]:
        """版本族 = 同一 (case_id, kind) 的所有 Artifact（create_artifact 的
        version 即按该族递增），按版本号升序返回。"""
        current = await self.get_artifact(artifact_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.case_id == current.case_id,
                    ArtifactRecord.kind == current.kind,
                )
                .order_by(ArtifactRecord.version.asc())
            )
            return result.all()

    async def list_agent_runs(
        self, case_id: str
    ) -> Sequence[AgentRunRecord]:
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(AgentRunRecord)
                .where(AgentRunRecord.case_id == case_id)
                .order_by(AgentRunRecord.created_at.asc())
            )
            return result.all()

    async def get_latest_artifact(
        self,
        case_id: str,
        kind: str,
    ) -> ArtifactRecord | None:
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.case_id == case_id,
                    ArtifactRecord.kind == kind,
                )
                .order_by(ArtifactRecord.version.desc())
                .limit(1)
            )

    async def list_run_artifacts(
        self,
        run_id: str,
    ) -> Sequence[ArtifactRecord]:
        await self.get_agent_run(run_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(ArtifactRecord)
                .where(ArtifactRecord.run_id == run_id)
                .order_by(ArtifactRecord.version.asc())
            )
            return result.all()

    # ------------------------------------------------------------------
    # Run steering (instructions injected into a running coordinator run)
    # ------------------------------------------------------------------

    async def add_run_steering(
        self,
        run_id: str,
        content: str,
    ) -> RunSteeringRecord:
        await self.get_agent_run(run_id)
        record = RunSteeringRecord(run_id=run_id, content=content)
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_unconsumed_steerings(
        self,
        run_id: str,
    ) -> Sequence[RunSteeringRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(RunSteeringRecord)
                .where(
                    RunSteeringRecord.run_id == run_id,
                    RunSteeringRecord.consumed_at.is_(None),
                )
                .order_by(RunSteeringRecord.created_at.asc())
            )
            return result.all()

    async def mark_steerings_consumed(self, run_id: str) -> None:
        now = datetime.now(UTC)
        async with self._database.session_factory() as session:
            records = await session.scalars(
                select(RunSteeringRecord).where(
                    RunSteeringRecord.run_id == run_id,
                    RunSteeringRecord.consumed_at.is_(None),
                )
            )
            for record in records:
                record.consumed_at = now
            await session.commit()

    # ------------------------------------------------------------------
    # Agent mailbox (typed messages between parent and child runs)
    # ------------------------------------------------------------------

    async def add_agent_message(
        self,
        *,
        sender_run_id: str,
        receiver_run_id: str,
        message_type: str,
        payload: dict[str, object],
    ) -> AgentMessageRecord:
        await self.get_agent_run(sender_run_id)
        record = AgentMessageRecord(
            sender_run_id=sender_run_id,
            receiver_run_id=receiver_run_id,
            message_type=message_type,
            payload=payload,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_agent_messages(
        self,
        run_id: str,
        *,
        sender_run_id: str | None = None,
        receiver_run_id: str | None = None,
    ) -> Sequence[AgentMessageRecord]:
        await self.get_agent_run(run_id)
        filters = [
            or_(
                AgentMessageRecord.sender_run_id == run_id,
                AgentMessageRecord.receiver_run_id == run_id,
            )
        ]
        if sender_run_id is not None:
            filters.append(AgentMessageRecord.sender_run_id == sender_run_id)
        if receiver_run_id is not None:
            filters.append(AgentMessageRecord.receiver_run_id == receiver_run_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(AgentMessageRecord)
                .where(*filters)
                .order_by(AgentMessageRecord.created_at.asc())
            )
            return result.all()

    async def create_agent_run(
        self,
        *,
        case_id: str,
        turn_id: str | None,
        objective: str,
        agent: str = "coordinator",
        model_route: str = "fast",
        parent_run_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AgentRunRecord:
        await self.get_case(case_id)
        record = AgentRunRecord(
            case_id=case_id,
            turn_id=turn_id,
            agent=agent,
            status="pending",
            objective=objective,
            model_route=model_route,
            parent_run_id=parent_run_id,
            metadata_json=metadata or {},
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_child_runs(self, parent_run_id: str) -> Sequence[AgentRunRecord]:
        await self.get_agent_run(parent_run_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(AgentRunRecord).where(
                    AgentRunRecord.parent_run_id == parent_run_id
                )
            )
            return result.all()

    async def get_child_run_by_dispatch_key(
        self,
        parent_run_id: str,
        dispatch_key: str,
    ) -> AgentRunRecord | None:
        """Return the child run created for a dispatch key, if any.

        Used to make expert dispatch idempotent across checkpoint replays.
        """
        await self.get_agent_run(parent_run_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(AgentRunRecord).where(
                    AgentRunRecord.parent_run_id == parent_run_id,
                )
            )
            for run in result:
                metadata = run.metadata_json or {}
                dispatch = metadata.get("dispatch")
                if isinstance(dispatch, dict) and dispatch.get("dispatch_key") == dispatch_key:
                    return run
            return None

    async def get_agent_run(self, run_id: str) -> AgentRunRecord:
        async with self._database.session_factory() as session:
            record = await session.get(AgentRunRecord, run_id)
            if record is None:
                raise ResourceNotFoundError("agent_run", run_id)
            return record

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        turn_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        tool_call_count: int | None = None,
        estimated_cost: float | None = None,
        error_code: str | None = None,
        error: str | None = None,
    ) -> AgentRunRecord:
        async with self._database.session_factory() as session:
            record = await session.get(AgentRunRecord, run_id)
            if record is None:
                raise ResourceNotFoundError("agent_run", run_id)
            if status is not None:
                record.status = status
            if turn_id is not None:
                record.turn_id = turn_id
            if input_tokens is not None:
                record.input_tokens = input_tokens
            if output_tokens is not None:
                record.output_tokens = output_tokens
            if tool_call_count is not None:
                record.tool_call_count = tool_call_count
            if estimated_cost is not None:
                record.estimated_cost = estimated_cost
            record.error_code = error_code
            record.error = error
            await session.commit()
            await session.refresh(record)
            return record

    async def patch_run_metadata(
        self,
        run_id: str,
        patch: dict[str, object],
    ) -> AgentRunRecord:
        """Merge keys into ``agent_runs.metadata_json`` (approval scope/budget)."""
        async with self._database.session_factory() as session:
            record = await session.get(AgentRunRecord, run_id)
            if record is None:
                raise ResourceNotFoundError("agent_run", run_id)
            current = dict(record.metadata_json or {})
            current.update(patch)
            record.metadata_json = current
            await session.commit()
            await session.refresh(record)
            return record

    async def add_model_call(
        self,
        *,
        call_id: str,
        run_id: str,
        model: str,
        route: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        estimated_cost: float,
        currency: str,
        pricing_model: str | None,
        latency_ms: int,
    ) -> ModelCallRecord:
        record = ModelCallRecord(
            id=call_id,
            run_id=run_id,
            model=model,
            route=route,
            status="completed",
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            currency=currency,
            pricing_model=pricing_model,
            latency_ms=latency_ms,
        )
        async with self._database.session_factory() as session:
            existing = await session.get(ModelCallRecord, call_id)
            if existing is not None:
                return existing
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def add_run_event(
        self,
        run_id: str,
        payload: dict[str, object],
    ) -> RunEventRecord:
        await self.get_agent_run(run_id)
        record = RunEventRecord(
            run_id=run_id,
            event_type=str(payload.get("event_type") or "runtime"),
            agent=str(payload.get("agent") or ""),
            skill=(
                str(payload["skill"]) if payload.get("skill") is not None else None
            ),
            tool_call_id=(
                str(payload["tool_call_id"])
                if payload.get("tool_call_id") is not None
                else None
            ),
            tool=str(payload["tool"]) if payload.get("tool") is not None else None,
            status=str(payload.get("status") or "running"),
            payload=payload,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_run_events(
        self,
        run_id: str,
        *,
        after_id: int = 0,
    ) -> Sequence[RunEventRecord]:
        await self.get_agent_run(run_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(RunEventRecord)
                .where(
                    RunEventRecord.run_id == run_id,
                    RunEventRecord.id > after_id,
                )
                .order_by(RunEventRecord.id.asc())
            )
            return result.all()

    # ------------------------------------------------------------------
    # Tool calls (durable audit trail, idempotent by idempotency_key)
    # ------------------------------------------------------------------

    async def add_tool_call(
        self,
        *,
        call_id: str,
        run_id: str,
        tool_name: str,
        skill_name: str | None,
        status: str,
        arguments: dict[str, object] | None = None,
        result: dict[str, object] | None = None,
        error_code: str | None = None,
        input_summary: str | None = None,
        output_summary: str | None = None,
        retry_count: int = 0,
        retry_history: list[dict[str, object]] | None = None,
        cached: bool = False,
        duration_ms: int = 0,
        estimated_cost: float = 0,
        idempotency_key: str | None = None,
        approval_id: str | None = None,
        rag: dict[str, object] | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> ToolCallRecord:
        await self.get_agent_run(run_id)
        async with self._database.session_factory() as session:
            if idempotency_key is not None:
                existing = await session.scalar(
                    select(ToolCallRecord).where(
                        ToolCallRecord.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    return existing
            record = ToolCallRecord(
                id=call_id,
                run_id=run_id,
                tool_name=tool_name,
                skill_name=skill_name,
                status=status,
                arguments=arguments or {},
                result=result or {},
                error_code=error_code,
                input_summary=input_summary,
                output_summary=output_summary,
                retry_count=retry_count,
                retry_history=retry_history or [],
                cached=cached,
                duration_ms=duration_ms,
                estimated_cost=estimated_cost,
                idempotency_key=idempotency_key,
                approval_id=approval_id,
                rag=rag,
                started_at=started_at or datetime.now(UTC),
                finished_at=finished_at,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_run_tool_calls(
        self,
        run_id: str,
    ) -> Sequence[ToolCallRecord]:
        await self.get_agent_run(run_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(ToolCallRecord)
                .where(ToolCallRecord.run_id == run_id)
                .order_by(ToolCallRecord.started_at.asc())
            )
            return result.all()

    async def update_tool_call(
        self,
        run_id: str,
        call_id: str,
        *,
        status: str | None = None,
        result: dict[str, object] | None = None,
        error_code: str | None = None,
        output_summary: str | None = None,
        duration_ms: int | None = None,
        retry_count: int | None = None,
        retry_history: list[dict[str, object]] | None = None,
        cached: bool | None = None,
        estimated_cost: float | None = None,
        approval_id: str | None = None,
    ) -> ToolCallRecord:
        async with self._database.session_factory() as session:
            record = await session.get(ToolCallRecord, call_id)
            if record is None or record.run_id != run_id:
                raise ResourceNotFoundError("tool_call", call_id)
            if status is not None:
                record.status = status
            if result is not None:
                record.result = result
            record.error_code = error_code
            if output_summary is not None:
                record.output_summary = output_summary
            if duration_ms is not None:
                record.duration_ms = duration_ms
            if retry_count is not None:
                record.retry_count = retry_count
            if retry_history is not None:
                record.retry_history = retry_history
            if cached is not None:
                record.cached = cached
            if estimated_cost is not None:
                record.estimated_cost = estimated_cost
            if approval_id is not None:
                record.approval_id = approval_id
            record.finished_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(record)
            return record

    # ------------------------------------------------------------------
    # Approvals (in-flight human-in-the-loop decisions)
    # ------------------------------------------------------------------

    async def create_approval(
        self,
        *,
        run_id: str,
        action: str,
        reason: str,
        request_payload: dict[str, object],
    ) -> ApprovalRecord:
        await self.get_agent_run(run_id)
        record = ApprovalRecord(
            run_id=run_id,
            action=action,
            reason=reason,
            status="pending",
            request_payload=request_payload,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_approval(self, approval_id: str) -> ApprovalRecord:
        async with self._database.session_factory() as session:
            record = await session.get(ApprovalRecord, approval_id)
            if record is None:
                raise ResourceNotFoundError("approval", approval_id)
            return record

    async def list_run_approvals(
        self,
        run_id: str,
    ) -> Sequence[ApprovalRecord]:
        await self.get_agent_run(run_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(ApprovalRecord)
                .where(ApprovalRecord.run_id == run_id)
                .order_by(ApprovalRecord.created_at.asc())
            )
            return result.all()

    async def update_approval(
        self,
        approval_id: str,
        *,
        status: str,
        decision_payload: dict[str, object],
    ) -> ApprovalRecord:
        await self.get_approval(approval_id)
        async with self._database.session_factory() as session:
            current = await session.get(ApprovalRecord, approval_id)
            assert current is not None
            current.status = status
            current.decision_payload = decision_payload
            current.decided_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(current)
        return current

    async def list_pending_approvals(self, run_id: str) -> Sequence[ApprovalRecord]:
        await self.get_agent_run(run_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(ApprovalRecord)
                .where(
                    ApprovalRecord.run_id == run_id,
                    ApprovalRecord.status == "pending",
                )
                .order_by(ApprovalRecord.created_at.asc())
            )
            return result.all()

    async def get_latest_decided_approval(
        self,
        run_id: str,
    ) -> ApprovalRecord | None:
        await self.get_agent_run(run_id)
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(ApprovalRecord)
                .where(
                    ApprovalRecord.run_id == run_id,
                    ApprovalRecord.status.in_(
                        [
                            "approved",
                            "approved_with_edits",
                            "rejected",
                            "cancelled",
                            "expired",
                        ]
                    ),
                )
                .order_by(ApprovalRecord.decided_at.desc())
                .limit(1)
            )
            return record

    # ------------------------------------------------------------------
    # Claims, evidence and propagation edges (domain graph)
    # ------------------------------------------------------------------

    async def create_claim(
        self,
        *,
        case_id: str,
        text: str,
        created_by_run_id: str,
    ) -> ClaimRecord:
        await self.get_case(case_id)
        record = ClaimRecord(
            case_id=case_id,
            text=text,
            status="open",
            created_by_run_id=created_by_run_id,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_claims_by_case(
        self,
        case_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> Sequence[ClaimRecord]:
        await self.get_case(case_id)
        query = select(ClaimRecord).where(ClaimRecord.case_id == case_id)
        if status is not None:
            query = query.where(ClaimRecord.status == status)
        query = query.order_by(ClaimRecord.created_at.asc()).limit(limit)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def get_claim(self, claim_id: str) -> ClaimRecord:
        async with self._database.session_factory() as session:
            record = await session.get(ClaimRecord, claim_id)
            if record is None:
                raise ResourceNotFoundError("claim", claim_id)
            return record

    async def review_claim(
        self,
        case_id: str,
        claim_id: str,
        *,
        confirmed: bool,
        note: str | None = None,
    ) -> ClaimRecord:
        """Human review of a verification card.

        Updates ``claims.status`` and writes an ``evaluations`` row so the
        decision is auditable independently of the automatic verdict.
        """
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            record = await session.get(ClaimRecord, claim_id)
            if record is None or record.case_id != case_id:
                raise ResourceNotFoundError("claim", claim_id)
            record.status = "human_confirmed" if confirmed else "human_rejected"
            await session.commit()
            await session.refresh(record)
        await self.create_evaluation(
            case_id=case_id,
            run_id=None,
            metric="claim_human_review",
            score=1.0 if confirmed else 0.0,
            details={"claim_id": claim_id, "note": note or "", "confirmed": confirmed},
        )
        return record

    async def update_claim_verdict(
        self,
        claim_id: str,
        *,
        verdict: str,
        status: str,
        confidence: float,
    ) -> ClaimRecord:
        async with self._database.session_factory() as session:
            record = await session.get(ClaimRecord, claim_id)
            if record is None:
                raise ResourceNotFoundError("claim", claim_id)
            record.verdict = verdict
            record.status = status
            record.confidence = confidence
            await session.commit()
            await session.refresh(record)
            return record

    async def create_evidence(
        self,
        *,
        case_id: str,
        claim_id: str | None = None,
        source_type: str,
        source_id: str,
        stance: str,
        excerpt: str,
        relevance: float = 0.0,
        metadata: dict[str, object] | None = None,
    ) -> EvidenceRecord:
        """Create an evidence row, idempotent per (case, source, claim)."""
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            query = select(EvidenceRecord).where(
                EvidenceRecord.case_id == case_id,
                EvidenceRecord.source_type == source_type,
                EvidenceRecord.source_id == source_id,
            )
            if claim_id is None:
                query = query.where(EvidenceRecord.claim_id.is_(None))
            else:
                query = query.where(EvidenceRecord.claim_id == claim_id)
            existing = await session.scalar(query)
            if existing is not None:
                return existing
            record = EvidenceRecord(
                case_id=case_id,
                claim_id=claim_id,
                source_type=source_type,
                source_id=source_id,
                stance=stance,
                excerpt=excerpt,
                relevance=relevance,
                metadata_json=metadata or {},
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_evidence_by_claim(
        self,
        claim_id: str,
    ) -> Sequence[EvidenceRecord]:
        await self.get_claim(claim_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(EvidenceRecord)
                .where(EvidenceRecord.claim_id == claim_id)
                .order_by(EvidenceRecord.relevance.desc())
            )
            return result.all()

    async def list_evidence_by_case(
        self,
        case_id: str,
        *,
        source_type: str | None = None,
        limit: int = 200,
    ) -> Sequence[EvidenceRecord]:
        await self.get_case(case_id)
        query = select(EvidenceRecord).where(EvidenceRecord.case_id == case_id)
        if source_type is not None:
            query = query.where(EvidenceRecord.source_type == source_type)
        query = query.order_by(EvidenceRecord.relevance.desc()).limit(limit)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def get_claim_evidence_quality_metrics(
        self, case_id: str
    ) -> dict[str, object]:
        """V3 §12.2: Evidence Coverage 批量指标（一次聚合，禁止逐 claim N+1）。

        Evidence↔Claim 关联直接使用 EvidenceRecord.claim_id。
        """
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            claims_total = await session.scalar(
                select(func.count(ClaimRecord.id)).where(
                    ClaimRecord.case_id == case_id
                )
            )
            claims_with_evidence = await session.scalar(
                select(
                    func.count(func.distinct(EvidenceRecord.claim_id))
                ).where(
                    EvidenceRecord.case_id == case_id,
                    EvidenceRecord.claim_id.isnot(None),
                )
            )
            evidence_total = await session.scalar(
                select(func.count(EvidenceRecord.id)).where(
                    EvidenceRecord.case_id == case_id
                )
            )
            latest_claim_at = await session.scalar(
                select(func.max(ClaimRecord.created_at)).where(
                    ClaimRecord.case_id == case_id
                )
            )
            latest_evidence_at = await session.scalar(
                select(func.max(EvidenceRecord.created_at)).where(
                    EvidenceRecord.case_id == case_id
                )
            )
        return {
            "claims_total": int(claims_total or 0),
            "claims_with_evidence": int(claims_with_evidence or 0),
            "evidence_total": int(evidence_total or 0),
            "latest_claim_at": latest_claim_at,
            "latest_evidence_at": latest_evidence_at,
        }

    async def get_review_decision_quality_metrics(
        self, case_id: str
    ) -> dict[str, object]:
        """V3 §22 fingerprint 输入：Case 级 ReviewDecision count + latest。

        ReviewDecision 无 case_id，必须 JOIN ReviewItem 限定 Case scope
        （沿用 V2 agent DB tools 的防跨 Case 泄漏约束）。
        """
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            total = await session.scalar(
                select(func.count(ReviewDecisionRecord.id))
                .select_from(ReviewDecisionRecord)
                .join(
                    ReviewItemRecord,
                    ReviewDecisionRecord.item_id == ReviewItemRecord.id,
                )
                .where(ReviewItemRecord.case_id == case_id)
            )
            latest = await session.scalar(
                select(func.max(ReviewDecisionRecord.created_at))
                .select_from(ReviewDecisionRecord)
                .join(
                    ReviewItemRecord,
                    ReviewDecisionRecord.item_id == ReviewItemRecord.id,
                )
                .where(ReviewItemRecord.case_id == case_id)
            )
        return {
            "review_decision_count": int(total or 0),
            "latest_review_decision_at": latest,
        }

    async def get_finding_link_integrity_metrics(
        self, case_id: str
    ) -> dict[str, object]:
        """V3 §18 Provenance Integrity：Finding evidence/source link dangling 检查。

        checked_refs = 当前 case 全部 FindingEvidenceLink + FindingSourceLink；
        dangling 分级：verified Finding 上的 dangling → critical，其余 → warning。
        """
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            findings = (
                await session.scalars(
                    select(FindingRecord).where(FindingRecord.case_id == case_id)
                )
            ).all()
            finding_ids = [finding.id for finding in findings]
            if not finding_ids:
                return {
                    "checked_refs": 0,
                    "dangling_refs": 0,
                    "critical_dangling": [],
                    "warning_dangling": [],
                }
            status_by_finding = {finding.id: finding.status for finding in findings}
            evidence_links = (
                await session.scalars(
                    select(FindingEvidenceLinkRecord).where(
                        FindingEvidenceLinkRecord.finding_id.in_(finding_ids)
                    )
                )
            ).all()
            source_links = (
                await session.scalars(
                    select(FindingSourceLinkRecord).where(
                        FindingSourceLinkRecord.finding_id.in_(finding_ids)
                    )
                )
            ).all()
            evidence_ids = {link.evidence_ref for link in evidence_links}
            existing_evidence: set[str] = set()
            if evidence_ids:
                rows = await session.scalars(
                    select(EvidenceRecord.id).where(
                        EvidenceRecord.id.in_(evidence_ids),
                        EvidenceRecord.case_id == case_id,
                    )
                )
                existing_evidence = set(rows.all())
            artifact_ids = {
                link.source_id
                for link in source_links
                if link.source_type == "artifact"
            }
            existing_artifacts: set[str] = set()
            if artifact_ids:
                rows = await session.scalars(
                    select(ArtifactRecord.id).where(
                        ArtifactRecord.id.in_(artifact_ids),
                        ArtifactRecord.case_id == case_id,
                    )
                )
                existing_artifacts = set(rows.all())
            checked = 0
            critical: list[dict[str, str]] = []
            warning: list[dict[str, str]] = []
            for link in evidence_links:
                checked += 1
                if link.evidence_ref in existing_evidence:
                    continue
                entry = {
                    "object_type": "finding_evidence_link",
                    "object_id": link.finding_id,
                    "ref": link.evidence_ref,
                }
                if status_by_finding.get(link.finding_id) == "verified":
                    critical.append(entry)
                else:
                    warning.append(entry)
            for link in source_links:
                checked += 1
                if link.source_type == "artifact":
                    if link.source_id in existing_artifacts:
                        continue
                # 未知 source_type 视为 dangling（当前唯一合法值为 artifact）
                entry = {
                    "object_type": "finding_source_link",
                    "object_id": link.finding_id,
                    "ref": f"{link.source_type}:{link.source_id}",
                }
                if status_by_finding.get(link.finding_id) == "verified":
                    critical.append(entry)
                else:
                    warning.append(entry)
            return {
                "checked_refs": checked,
                "dangling_refs": len(critical) + len(warning),
                "critical_dangling": critical,
                "warning_dangling": warning,
            }

    async def create_propagation_edge(
        self,
        *,
        case_id: str,
        source_post_id: str,
        target_post_id: str,
        relation: str,
        confidence: float,
        feature_scores: dict[str, object],
        evidence_ids: list[str],
        algorithm_version: str = "1.0.0",
    ) -> PropagationEdgeRecord:
        """Create a propagation edge, idempotent per (case, source, target)."""
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            existing = await session.scalar(
                select(PropagationEdgeRecord).where(
                    PropagationEdgeRecord.case_id == case_id,
                    PropagationEdgeRecord.source_post_id == source_post_id,
                    PropagationEdgeRecord.target_post_id == target_post_id,
                )
            )
            if existing is not None:
                return existing
            record = PropagationEdgeRecord(
                case_id=case_id,
                source_post_id=source_post_id,
                target_post_id=target_post_id,
                relation=relation,
                confidence=confidence,
                feature_scores=feature_scores,
                evidence_ids=evidence_ids,
                algorithm_version=algorithm_version,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_propagation_edges_by_case(
        self,
        case_id: str,
        *,
        relation: str | None = None,
        min_confidence: float | None = None,
        limit: int = 500,
    ) -> Sequence[PropagationEdgeRecord]:
        await self.get_case(case_id)
        query = select(PropagationEdgeRecord).where(
            PropagationEdgeRecord.case_id == case_id
        )
        if relation is not None:
            query = query.where(PropagationEdgeRecord.relation == relation)
        if min_confidence is not None:
            query = query.where(
                PropagationEdgeRecord.confidence >= min_confidence
            )
        query = query.order_by(
            PropagationEdgeRecord.confidence.desc()
        ).limit(limit)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def confirm_propagation_edge(
        self,
        case_id: str,
        edge_id: str,
        *,
        confirmed: bool,
        note: str | None = None,
    ) -> PropagationEdgeRecord:
        """Flip the human confirmation state of a propagation edge.

        The edge must belong to the case; every decision is appended to the
        ``evaluations`` table so the manual review stays auditable.
        """
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(PropagationEdgeRecord).where(
                    PropagationEdgeRecord.id == edge_id,
                    PropagationEdgeRecord.case_id == case_id,
                )
            )
            if record is None:
                raise ResourceNotFoundError("propagation edge", edge_id)
            # FC1: explicit tri-state. human_confirmed stays as a compatibility
            # mirror (confirmed -> True, otherwise False).
            review_state = "confirmed" if confirmed else "rejected"
            record.human_review_state = review_state
            record.human_confirmed = confirmed
            await session.commit()
            await session.refresh(record)
        await self.create_evaluation(
            case_id=case_id,
            run_id=None,
            metric="propagation_edge_human_confirmation",
            score=1.0 if confirmed else 0.0,
            details={
                "edge_id": edge_id,
                "propagation_edge_id": edge_id,
                "human_review_state": review_state,
                "confirmed": confirmed,
                "note": note or "",
            },
        )
        return record

    # ------------------------------------------------------------------
    # Worker leases: atomic claim, release and refresh.
    # ------------------------------------------------------------------

    async def claim_agent_run(
        self,
        worker_id: str,
        lease_seconds: int,
    ) -> AgentRunRecord | None:
        """Atomically claim the oldest eligible run for this worker.

        SQLite ignores FOR UPDATE; PostgreSQL uses SKIP LOCKED so two
        workers can never claim the same run.
        """
        now = datetime.now(UTC)
        async with self._database.session_factory() as session:
            run_id = await session.scalar(
                select(AgentRunRecord.id)
                .where(
                    AgentRunRecord.status.in_(["pending", "running"]),
                    or_(
                        AgentRunRecord.lease_expires_at.is_(None),
                        AgentRunRecord.lease_expires_at < now,
                    ),
                )
                .order_by(AgentRunRecord.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if run_id is None:
                await session.commit()
                return None
            record = await session.get(AgentRunRecord, run_id)
            assert record is not None
            record.lease_owner = worker_id
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            await session.commit()
            await session.refresh(record)
            return record

    async def refresh_agent_run_lease(
        self,
        run_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> AgentRunRecord:
        await self.get_agent_run(run_id)
        async with self._database.session_factory() as session:
            current = await session.get(AgentRunRecord, run_id)
            assert current is not None
            current.lease_owner = worker_id
            current.lease_expires_at = datetime.now(UTC) + timedelta(
                seconds=lease_seconds
            )
            await session.commit()
            await session.refresh(current)
        return current

    async def release_agent_run(
        self,
        run_id: str,
        worker_id: str,
    ) -> AgentRunRecord:
        await self.get_agent_run(run_id)
        async with self._database.session_factory() as session:
            current = await session.get(AgentRunRecord, run_id)
            assert current is not None
            if current.lease_owner == worker_id:
                current.lease_owner = None
                current.lease_expires_at = None
            await session.commit()
            await session.refresh(current)
        return current

    # ------------------------------------------------------------------
    # Run trace aggregation
    # ------------------------------------------------------------------

    async def get_run_trace(self, run_id: str) -> dict[str, object]:
        run = await self.get_agent_run(run_id)
        model_calls, tool_calls, approvals, events = await self._gather_run_children(
            run_id
        )
        return {
            "run": run,
            "model_calls": model_calls,
            "tool_calls": tool_calls,
            "approvals": approvals,
            "events": events,
        }

    async def _gather_run_children(
        self, run_id: str
    ) -> tuple[
        Sequence[ModelCallRecord],
        Sequence[ToolCallRecord],
        Sequence[ApprovalRecord],
        Sequence[RunEventRecord],
    ]:
        async with self._database.session_factory() as session:
            model_calls = (
                await session.scalars(
                    select(ModelCallRecord)
                    .where(ModelCallRecord.run_id == run_id)
                    .order_by(ModelCallRecord.created_at.asc())
                )
            ).all()
            tool_calls = (
                await session.scalars(
                    select(ToolCallRecord)
                    .where(ToolCallRecord.run_id == run_id)
                    .order_by(ToolCallRecord.started_at.asc())
                )
            ).all()
            approvals = (
                await session.scalars(
                    select(ApprovalRecord)
                    .where(ApprovalRecord.run_id == run_id)
                    .order_by(ApprovalRecord.created_at.asc())
                )
            ).all()
            events = (
                await session.scalars(
                    select(RunEventRecord)
                    .where(RunEventRecord.run_id == run_id)
                    .order_by(RunEventRecord.id.asc())
                )
            ).all()
        return model_calls, tool_calls, approvals, events

    # ------------------------------------------------------------------
    # Domain models (M7): accounts, media assets, entities, propagation
    # nodes, evaluations and cost summaries
    # ------------------------------------------------------------------

    async def upsert_account(
        self,
        *,
        case_id: str | None,
        platform: str,
        native_id: str,
        name: str,
        normalized_name: str,
        avatar_url: str | None = None,
        follower_count: int = 0,
        verified: bool = False,
        is_authoritative: bool = False,
        metadata: dict[str, object] | None = None,
    ) -> AccountRecord:
        """Create or update an account, idempotent per (platform, native_id)."""
        async with self._database.session_factory() as session:
            existing = await session.scalar(
                select(AccountRecord).where(
                    AccountRecord.platform == platform,
                    AccountRecord.native_id == native_id,
                )
            )
            if existing is not None:
                existing.name = name or existing.name
                existing.normalized_name = normalized_name or existing.normalized_name
                if follower_count:
                    existing.follower_count = follower_count
                existing.verified = verified
                existing.is_authoritative = (
                    existing.is_authoritative or is_authoritative
                )
                await session.commit()
                await session.refresh(existing)
                return existing
            record = AccountRecord(
                case_id=case_id,
                platform=platform,
                native_id=native_id,
                name=name,
                normalized_name=normalized_name,
                avatar_url=avatar_url,
                follower_count=follower_count,
                verified=verified,
                is_authoritative=is_authoritative,
                metadata_json=metadata or {},
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def get_account_by_native_id(
        self,
        platform: str,
        native_id: str,
    ) -> AccountRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(AccountRecord).where(
                    AccountRecord.platform == platform,
                    AccountRecord.native_id == native_id,
                )
            )

    async def list_accounts(
        self,
        *,
        case_id: str | None = None,
        platform: str | None = None,
        limit: int = 200,
    ) -> Sequence[AccountRecord]:
        query = select(AccountRecord)
        if case_id is not None:
            query = query.where(AccountRecord.case_id == case_id)
        if platform is not None:
            query = query.where(AccountRecord.platform == platform)
        query = query.order_by(AccountRecord.name.asc()).limit(limit)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def list_authoritative_accounts(
        self,
        *,
        platform: str | None = None,
        limit: int = 200,
    ) -> Sequence[AccountRecord]:
        """Official-account whitelist used by the Verification agent."""
        query = select(AccountRecord).where(
            AccountRecord.is_authoritative.is_(True)
        )
        if platform is not None:
            query = query.where(AccountRecord.platform == platform)
        query = query.order_by(AccountRecord.platform.asc()).limit(limit)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def create_media_asset(
        self,
        *,
        case_id: str,
        post_id: str | None,
        platform: str,
        media_type: str,
        url: str,
        normalized_url: str,
        file_sha256: str | None = None,
        phash: str | None = None,
        ocr_text: str | None = None,
        keyframe_urls: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MediaAssetRecord:
        """Create a media asset, idempotent per (case, normalized_url, post)."""
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            existing = await session.scalar(
                select(MediaAssetRecord).where(
                    MediaAssetRecord.case_id == case_id,
                    MediaAssetRecord.normalized_url == normalized_url,
                    MediaAssetRecord.post_id == post_id,
                )
            )
            if existing is not None:
                return existing
            record = MediaAssetRecord(
                case_id=case_id,
                post_id=post_id,
                platform=platform,
                media_type=media_type,
                url=url,
                normalized_url=normalized_url,
                file_sha256=file_sha256,
                phash=phash,
                ocr_text=ocr_text,
                keyframe_urls=keyframe_urls or [],
                metadata_json=metadata or {},
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_media_assets_by_url(
        self,
        case_id: str,
        normalized_url: str,
    ) -> Sequence[MediaAssetRecord]:
        """Find all posts of a case that reference the same normalized media."""
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(MediaAssetRecord).where(
                    MediaAssetRecord.case_id == case_id,
                    MediaAssetRecord.normalized_url == normalized_url,
                )
            )
            return result.all()

    async def upsert_entity(
        self,
        *,
        case_id: str,
        entity_type: str,
        name: str,
        normalized_name: str,
        aliases: list[str] | None = None,
        seen_at: datetime | None = None,
    ) -> EntityRecord:
        """Create or bump an entity's mention count (case, type, name)."""
        await self.get_case(case_id)
        seen_at = seen_at or datetime.now(UTC)
        async with self._database.session_factory() as session:
            existing = await session.scalar(
                select(EntityRecord).where(
                    EntityRecord.case_id == case_id,
                    EntityRecord.entity_type == entity_type,
                    EntityRecord.normalized_name == normalized_name,
                )
            )
            if existing is not None:
                existing.mentions_count += 1
                if aliases:
                    existing.aliases = sorted(
                        set(existing.aliases) | set(aliases)
                    )
                last_seen = existing.last_seen_at
                if last_seen is not None and last_seen.tzinfo is None:
                    # SQLite returns naive datetimes; treat them as UTC.
                    last_seen = last_seen.replace(tzinfo=UTC)
                existing.last_seen_at = max(last_seen or seen_at, seen_at)
                if existing.first_seen_at is None:
                    existing.first_seen_at = seen_at
                await session.commit()
                await session.refresh(existing)
                return self._with_utc_timestamps(existing)
            record = EntityRecord(
                case_id=case_id,
                entity_type=entity_type,
                name=name,
                normalized_name=normalized_name,
                aliases=aliases or [],
                mentions_count=1,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return self._with_utc_timestamps(record)

    @staticmethod
    def _with_utc_timestamps(record: EntityRecord) -> EntityRecord:
        """SQLite round-trips aware datetimes as naive; restore UTC."""
        for field in ("first_seen_at", "last_seen_at"):
            value = getattr(record, field)
            if value is not None and value.tzinfo is None:
                setattr(record, field, value.replace(tzinfo=UTC))
        return record

    async def list_entities(
        self,
        case_id: str,
        *,
        entity_type: str | None = None,
        limit: int = 100,
    ) -> Sequence[EntityRecord]:
        await self.get_case(case_id)
        query = select(EntityRecord).where(EntityRecord.case_id == case_id)
        if entity_type is not None:
            query = query.where(EntityRecord.entity_type == entity_type)
        query = query.order_by(EntityRecord.mentions_count.desc()).limit(limit)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def create_propagation_node(
        self,
        *,
        case_id: str,
        post_id: str,
        role: str,
        score: float,
        attributes: dict[str, object] | None = None,
        algorithm_version: str = "1.0.0",
    ) -> PropagationNodeRecord:
        """Persist a computed node role, idempotent per (case, post, role)."""
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            existing = await session.scalar(
                select(PropagationNodeRecord).where(
                    PropagationNodeRecord.case_id == case_id,
                    PropagationNodeRecord.post_id == post_id,
                    PropagationNodeRecord.role == role,
                )
            )
            if existing is not None:
                return existing
            record = PropagationNodeRecord(
                case_id=case_id,
                post_id=post_id,
                role=role,
                score=score,
                attributes=attributes or {},
                algorithm_version=algorithm_version,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_propagation_nodes(
        self,
        case_id: str,
        *,
        role: str | None = None,
    ) -> Sequence[PropagationNodeRecord]:
        await self.get_case(case_id)
        query = select(PropagationNodeRecord).where(
            PropagationNodeRecord.case_id == case_id
        )
        if role is not None:
            query = query.where(PropagationNodeRecord.role == role)
        query = query.order_by(PropagationNodeRecord.score.desc())
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def list_propagation_graph(
        self, case_id: str
    ) -> tuple[
        Sequence[PropagationNodeRecord],
        Sequence[PropagationEdgeRecord],
        dict[str, SourcePostRecord],
    ]:
        """C7: 传播图一次装配 —— nodes + edges + 涉及的 source posts。

        只读取现有持久化表；posts 按图实际涉及的 post_id 过滤。
        """
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            nodes = (
                await session.scalars(
                    select(PropagationNodeRecord)
                    .where(PropagationNodeRecord.case_id == case_id)
                    .order_by(PropagationNodeRecord.score.desc())
                )
            ).all()
            edges = (
                await session.scalars(
                    select(PropagationEdgeRecord).where(
                        PropagationEdgeRecord.case_id == case_id
                    )
                )
            ).all()
            post_ids = {node.post_id for node in nodes}
            for edge in edges:
                post_ids.add(edge.source_post_id)
                post_ids.add(edge.target_post_id)
            posts: dict[str, SourcePostRecord] = {}
            if post_ids:
                rows = (
                    await session.scalars(
                        select(SourcePostRecord).where(
                            SourcePostRecord.id.in_(post_ids),
                            # case scope：跨 case 引用的 post 不提供元数据
                            SourcePostRecord.case_id == case_id,
                        )
                    )
                ).all()
                posts = {row.id: row for row in rows}
            return nodes, edges, posts

    async def create_evaluation(
        self,
        *,
        case_id: str | None,
        run_id: str | None,
        metric: str,
        score: float,
        details: dict[str, object] | None = None,
    ) -> EvaluationRecord:
        record = EvaluationRecord(
            case_id=case_id,
            run_id=run_id,
            metric=metric,
            score=score,
            details=details or {},
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_evaluations(
        self,
        *,
        case_id: str | None = None,
        run_id: str | None = None,
        metric: str | None = None,
        limit: int = 100,
    ) -> Sequence[EvaluationRecord]:
        query = select(EvaluationRecord)
        if case_id is not None:
            query = query.where(EvaluationRecord.case_id == case_id)
        if run_id is not None:
            query = query.where(EvaluationRecord.run_id == run_id)
        if metric is not None:
            query = query.where(EvaluationRecord.metric == metric)
        query = query.order_by(EvaluationRecord.created_at.desc()).limit(limit)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def upsert_cost_summary(
        self,
        *,
        summary_type: str,
        run_id: str | None = None,
        case_id: str | None = None,
        model_cost: float,
        tool_cost: float,
        total_cost: float,
        currency: str = "CNY",
        period: dict[str, object] | None = None,
    ) -> CostSummaryRecord:
        """Upsert a cost summary; a run may have at most one summary."""
        async with self._database.session_factory() as session:
            existing = await session.scalar(
                select(CostSummaryRecord).where(
                    CostSummaryRecord.summary_type == summary_type,
                    CostSummaryRecord.run_id == run_id,
                )
            )
            if existing is not None:
                existing.model_cost = model_cost
                existing.tool_cost = tool_cost
                existing.total_cost = total_cost
                existing.currency = currency
                if period is not None:
                    existing.period = period
                await session.commit()
                await session.refresh(existing)
                return existing
            record = CostSummaryRecord(
                summary_type=summary_type,
                run_id=run_id,
                case_id=case_id,
                model_cost=model_cost,
                tool_cost=tool_cost,
                total_cost=total_cost,
                currency=currency,
                period=period or {},
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_cost_summaries(
        self,
        *,
        run_id: str | None = None,
        case_id: str | None = None,
        limit: int = 50,
    ) -> Sequence[CostSummaryRecord]:
        query = select(CostSummaryRecord)
        if run_id is not None:
            query = query.where(CostSummaryRecord.run_id == run_id)
        if case_id is not None:
            query = query.where(CostSummaryRecord.case_id == case_id)
        query = query.order_by(CostSummaryRecord.created_at.desc()).limit(limit)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def upsert_embedding_version(
        self,
        *,
        model_name: str,
        model_version: str,
        dimensions: int,
        record_count: int,
    ) -> EmbeddingVersionRecord:
        """Register the embedding model version that just rebuilt vectors."""
        async with self._database.session_factory() as session:
            existing = await session.scalar(
                select(EmbeddingVersionRecord).where(
                    EmbeddingVersionRecord.model_version == model_version
                )
            )
            if existing is not None:
                existing.model_name = model_name
                existing.dimensions = dimensions
                existing.record_count = record_count
                existing.rebuilt_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(existing)
                return existing
            record = EmbeddingVersionRecord(
                model_name=model_name,
                model_version=model_version,
                dimensions=dimensions,
                record_count=record_count,
                rebuilt_at=datetime.now(UTC),
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def get_embedding_version(
        self,
        model_version: str,
    ) -> EmbeddingVersionRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(EmbeddingVersionRecord).where(
                    EmbeddingVersionRecord.model_version == model_version
                )
            )

    async def list_embedding_versions(
        self,
        limit: int = 10,
    ) -> Sequence[EmbeddingVersionRecord]:
        query = (
            select(EmbeddingVersionRecord)
            .order_by(EmbeddingVersionRecord.rebuilt_at.desc())
            .limit(limit)
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()
    # ---- 11 语义：词典 / 标注 / 纠错 / 模型版本 ---------------------------

    async def add_lexicon_entry(self, entry: LexiconEntryRecord) -> LexiconEntryRecord:
        async with self._database.session_factory() as session:
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return entry

    async def list_lexicon_entries(
        self,
        *,
        domain: str | None = None,
        platform: str | None = None,
        review_state: str | None = None,
        limit: int = 200,
    ) -> Sequence[LexiconEntryRecord]:
        query = select(LexiconEntryRecord)
        if domain:
            query = query.where(LexiconEntryRecord.domain == domain)
        if platform:
            query = query.where(LexiconEntryRecord.platform == platform)
        if review_state:
            query = query.where(LexiconEntryRecord.review_state == review_state)
        query = query.order_by(LexiconEntryRecord.updated_at.desc()).limit(limit)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def add_semantic_annotation(
        self, annotation: SemanticAnnotationRecord
    ) -> SemanticAnnotationRecord:
        async with self._database.session_factory() as session:
            session.add(annotation)
            await session.commit()
            await session.refresh(annotation)
            return annotation

    async def list_semantic_annotations(
        self,
        *,
        case_id: str | None = None,
        source_id: str | None = None,
        task: str | None = None,
        limit: int = 200,
    ) -> Sequence[SemanticAnnotationRecord]:
        query = select(SemanticAnnotationRecord)
        if case_id:
            query = query.where(SemanticAnnotationRecord.case_id == case_id)
        if source_id:
            query = query.where(SemanticAnnotationRecord.source_id == source_id)
        if task:
            query = query.where(SemanticAnnotationRecord.task == task)
        query = query.order_by(SemanticAnnotationRecord.created_at.desc()).limit(limit)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def get_semantic_annotation(
        self, annotation_id: str
    ) -> SemanticAnnotationRecord:
        async with self._database.session_factory() as session:
            record = await session.get(SemanticAnnotationRecord, annotation_id)
        if record is None:
            raise ResourceNotFoundError("semantic_annotation", annotation_id)
        return record

    async def add_annotation_correction(
        self, correction: AnnotationCorrectionRecord
    ) -> AnnotationCorrectionRecord:
        async with self._database.session_factory() as session:
            session.add(correction)
            await session.commit()
            await session.refresh(correction)
            return correction

    async def add_semantic_model_version(
        self, record: SemanticModelVersionRecord
    ) -> SemanticModelVersionRecord:
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_semantic_model_versions(
        self, *, component: str | None = None, limit: int = 20
    ) -> Sequence[SemanticModelVersionRecord]:
        query = select(SemanticModelVersionRecord)
        if component:
            query = query.where(SemanticModelVersionRecord.component == component)
        query = query.order_by(SemanticModelVersionRecord.created_at.desc()).limit(limit)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()
    # ---- 10 叙事：叙事 / 版本 / 成员 / 转换 / 纠错 / 生命周期 ----------------

    async def create_narrative(
        self, narrative: NarrativeRecord
    ) -> NarrativeRecord:
        async with self._database.session_factory() as session:
            session.add(narrative)
            await session.commit()
            await session.refresh(narrative)
            return narrative

    async def list_narratives(
        self, case_id: str, *, limit: int = 100
    ) -> Sequence[NarrativeRecord]:
        query = (
            select(NarrativeRecord)
            .where(NarrativeRecord.case_id == case_id)
            .order_by(NarrativeRecord.updated_at.desc())
            .limit(limit)
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def get_narrative(self, narrative_id: str) -> NarrativeRecord:
        async with self._database.session_factory() as session:
            record = await session.get(NarrativeRecord, narrative_id)
        if record is None:
            from app.core.errors import ResourceNotFoundError

            raise ResourceNotFoundError("narrative")
        return record

    async def add_narrative_version(
        self, version: NarrativeVersionRecord
    ) -> NarrativeVersionRecord:
        async with self._database.session_factory() as session:
            session.add(version)
            await session.commit()
            await session.refresh(version)
            return version

    async def list_narrative_versions(
        self, narrative_id: str, *, limit: int = 20
    ) -> Sequence[NarrativeVersionRecord]:
        query = (
            select(NarrativeVersionRecord)
            .where(NarrativeVersionRecord.narrative_id == narrative_id)
            .order_by(NarrativeVersionRecord.created_at.desc())
            .limit(limit)
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def add_narrative_claim(
        self, record: NarrativeClaimRecord
    ) -> NarrativeClaimRecord:
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def add_narrative_post(
        self, record: NarrativePostRecord
    ) -> NarrativePostRecord:
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def update_narrative_state(
        self, narrative: NarrativeRecord
    ) -> NarrativeRecord:
        async with self._database.session_factory() as session:
            record = await session.get(NarrativeRecord, narrative.id)
            if record is None:
                from app.core.errors import ResourceNotFoundError

                raise ResourceNotFoundError("narrative")
            record.status = narrative.status
            record.review_state = narrative.review_state
            await session.commit()
            await session.refresh(record)
            return record

    async def list_narrative_members(
        self, narrative_id: str
    ) -> dict[str, list[str]]:
        async with self._database.session_factory() as session:
            claims = (
                await session.scalars(
                    select(NarrativeClaimRecord).where(
                        NarrativeClaimRecord.narrative_id == narrative_id
                    )
                )
            ).all()
            posts = (
                await session.scalars(
                    select(NarrativePostRecord).where(
                        NarrativePostRecord.narrative_id == narrative_id
                    )
                )
            ).all()
            return {
                "claims": [c.claim_id for c in claims],
                "posts": [p.post_id for p in posts],
            }

    async def add_narrative_transition(
        self, record: NarrativeTransitionRecord
    ) -> NarrativeTransitionRecord:
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_narrative_transitions(
        self, narrative_id: str
    ) -> Sequence[NarrativeTransitionRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(NarrativeTransitionRecord)
                .where(NarrativeTransitionRecord.narrative_id == narrative_id)
                .order_by(NarrativeTransitionRecord.created_at.asc())
            )
            return result.all()

    async def remove_narrative_members(
        self,
        *,
        target_narrative_id: str,
        claim_ids: list[str],
        post_ids: list[str],
        decision_source: str,
    ) -> int:
        """撤销合并：从目标叙事移除指定决策来源的成员（仅限该来源）。"""
        from sqlalchemy import delete as sa_delete

        removed = 0
        async with self._database.session_factory() as session:
            if claim_ids:
                result = await session.execute(
                    sa_delete(NarrativeClaimRecord).where(
                        NarrativeClaimRecord.narrative_id == target_narrative_id,
                        NarrativeClaimRecord.claim_id.in_(claim_ids),
                        NarrativeClaimRecord.decision_source == decision_source,
                    )
                )
                removed += int(result.rowcount or 0)
            if post_ids:
                result = await session.execute(
                    sa_delete(NarrativePostRecord).where(
                        NarrativePostRecord.narrative_id == target_narrative_id,
                        NarrativePostRecord.post_id.in_(post_ids),
                        NarrativePostRecord.decision_source == decision_source,
                    )
                )
                removed += int(result.rowcount or 0)
            await session.commit()
        return removed

    async def add_correction_event(
        self, record: CorrectionEventRecord
    ) -> CorrectionEventRecord:
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_correction_events(
        self,
        case_id: str,
        *,
        target_narrative_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[CorrectionEventRecord]:
        query = select(CorrectionEventRecord).where(
            CorrectionEventRecord.case_id == case_id
        )
        if target_narrative_id:
            query = query.where(
                CorrectionEventRecord.target_narrative_id == target_narrative_id
            )
        query = query.order_by(CorrectionEventRecord.created_at.desc()).limit(limit)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def add_lifecycle_snapshot(
        self, record: LifecycleSnapshotRecord
    ) -> LifecycleSnapshotRecord:
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_lifecycle_snapshots(
        self, narrative_id: str, *, limit: int = 500
    ) -> Sequence[LifecycleSnapshotRecord]:
        query = (
            select(LifecycleSnapshotRecord)
            .where(LifecycleSnapshotRecord.narrative_id == narrative_id)
            .order_by(LifecycleSnapshotRecord.time_bucket.asc())
            .limit(limit)
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def add_correction_impact(
        self, record: CorrectionImpactAnalysisRecord
    ) -> CorrectionImpactAnalysisRecord:
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_correction_impacts(
        self, case_id: str, *, limit: int = 100
    ) -> Sequence[CorrectionImpactAnalysisRecord]:
        query = (
            select(CorrectionImpactAnalysisRecord)
            .where(CorrectionImpactAnalysisRecord.case_id == case_id)
            .order_by(CorrectionImpactAnalysisRecord.created_at.desc())
            .limit(limit)
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()
    # ---- 09 审核工作台：队列 / 领取 / 决策 / 评论 / 活动日志 ---------------

    async def create_review_item(
        self, item: ReviewItemRecord
    ) -> ReviewItemRecord:
        """通用 ReviewItem 创建（RC1.7：禁止绕过 finding 原子入口）。

        finding 类型必须走 submit_finding_for_review()，避免制造
        ReviewItem=unreviewed + Finding=candidate 或 dangling target 状态。
        其它 Review object（claim/evidence/...）不受影响。
        """
        if item.object_type == "finding":
            raise ApplicationError(
                "finding review item must use the atomic finding review submission path",
                code="review_finding_atomic_submit_required",
            )
        async with self._database.session_factory() as session:
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return item

    async def list_review_items(
        self,
        case_id: str,
        *,
        review_item_id: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[ReviewItemRecord]:
        query = select(ReviewItemRecord).where(
            ReviewItemRecord.case_id == case_id
        )
        if review_item_id:
            query = query.where(ReviewItemRecord.id == review_item_id)
        if status:
            query = query.where(ReviewItemRecord.status == status)
        if object_type:
            query = query.where(ReviewItemRecord.object_type == object_type)
        if object_id:
            query = query.where(ReviewItemRecord.object_id == object_id)
        query = (
            query.order_by(
                ReviewItemRecord.priority.desc(),
                ReviewItemRecord.created_at.asc(),
                ReviewItemRecord.id,
            )
            .limit(limit)
            .offset(offset)
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def get_review_item(self, item_id: str) -> ReviewItemRecord:
        async with self._database.session_factory() as session:
            record = await session.get(ReviewItemRecord, item_id)
        if record is None:
            from app.core.errors import ResourceNotFoundError

            raise ResourceNotFoundError("review_item")
        return record

    async def update_review_item_status(
        self, item_id: str, status: str
    ) -> ReviewItemRecord:
        # RH3: 通用 status writer（当前无生产调用，防御性保留）——只有真正
        # 改变 status 时递增 lifecycle revision；幂等设置同一状态不递增。
        async with self._database.session_factory() as session:
            record = await session.get(ReviewItemRecord, item_id)
            if record is None:
                from app.core.errors import ResourceNotFoundError

                raise ResourceNotFoundError("review_item")
            if record.status != status:
                record.status = status
                record.current_version += 1
            await session.commit()
            await session.refresh(record)
            return record

    async def claim_review_item(
        self, item_id: str, actor: str
    ) -> ReviewItemRecord | None:
        """Atomically claim an unreviewed item (unreviewed → in_review, version+1)."""
        from sqlalchemy import update as sa_update

        async with self._database.session_factory() as session:
            result = await session.execute(
                sa_update(ReviewItemRecord)
                .where(
                    ReviewItemRecord.id == item_id,
                    ReviewItemRecord.status == "unreviewed",
                )
                .values(
                    status="in_review",
                    current_version=ReviewItemRecord.current_version + 1,
                    updated_at=datetime.now(UTC),
                )
                .execution_options(synchronize_session=False)
            )
            if int(result.rowcount or 0) != 1:
                await session.rollback()
                return None
            session.add(
                ReviewAssignmentRecord(
                    item_id=item_id, actor=actor, status="active"
                )
            )
            await session.commit()
            record = await session.get(ReviewItemRecord, item_id)
            assert record is not None
            await session.refresh(record)
            return record

    async def _require_finding_review_target(
        self,
        session: Any,
        item: ReviewItemRecord,
    ) -> FindingRecord:
        """RC2/RC3 共享：校验 ReviewItem 的 Finding target 真实且同 Case。

        不存在或跨 Case 统一视为「没有合法的 case-scoped review target」，
        返回 review_object_not_found，避免通过 Review API 泄漏其它 Case
        是否存在同 ID 对象。
        """
        finding = await session.scalar(
            select(FindingRecord)
            .where(FindingRecord.id == item.object_id)
            .with_for_update()
        )
        if finding is None or finding.case_id != item.case_id:
            raise ApplicationError(
                "review finding target not found",
                code="review_object_not_found",
            )
        return finding

    async def decide_review_item(
        self,
        *,
        item_id: str,
        expected_status: str,
        expected_version: int,
        target_status: str,
        decision: ReviewDecisionRecord,
    ) -> tuple[ReviewItemRecord, ReviewDecisionRecord] | None:
        """Append a decision and update its item in one transaction (RH2 CAS).

        M4: 当对象是 finding 时，在同一事务内把 Review 状态映射同步到
        Finding.status（避免 Review 已 accepted 而 Finding 仍是 candidate）。

        RC2: finding decision 必须 fail closed —— ReviewItem 声称 object_type
        == finding 但 Finding 不存在/跨 Case 时整体失败，不写 ReviewItem/
        ReviewDecision；Finding 必须处于 under_review 才能被裁决为终审态；
        target status mapping 缺失也视为 defensive invariant 失败。

        RH2: 最终 winner 判定由数据库条件 UPDATE 完成 —— WHERE id AND
        status==expected_status AND current_version==expected_version，只有
        rowcount==1 的 transaction 才有权写 Finding 与 ReviewDecision；CAS
        失败者整体 rollback（0 ReviewDecision、0 Finding 变化），返回 None。
        ReviewItem.current_version 随状态变化 +1，ReviewDecision.object_version
        记录决策开始时的旧版本。
        """
        from sqlalchemy import update as sa_update

        from app.domain.enums import REVIEW_STATUS_TO_FINDING_STATUS

        async with self._database.session_factory() as session:
            item = await session.get(ReviewItemRecord, item_id)
            if item is None:
                await session.rollback()
                return None
            finding: FindingRecord | None = None
            if item.object_type == "finding":
                # 锁 Finding（与 submit 相同的对象访问顺序），校验 target
                # 真实且同 Case；mapping 缺失是静态 invariant，fail fast。
                finding = await self._require_finding_review_target(session, item)
                if REVIEW_STATUS_TO_FINDING_STATUS.get(target_status) is None:
                    raise ApplicationError(
                        "finding review status mapping missing",
                        code="review_finding_status_mapping_missing",
                    )
            # 数据库级 CAS：状态与版本都匹配的请求才获得唯一状态转换权。
            result = await session.execute(
                sa_update(ReviewItemRecord)
                .where(
                    ReviewItemRecord.id == item_id,
                    ReviewItemRecord.status == expected_status,
                    ReviewItemRecord.current_version == expected_version,
                )
                .values(
                    status=target_status,
                    current_version=ReviewItemRecord.current_version + 1,
                    updated_at=datetime.now(UTC),
                )
                .execution_options(synchronize_session=False)
            )
            if int(result.rowcount or 0) != 1:
                # CAS 失败者：0 ReviewDecision、0 Finding 变化。
                await session.rollback()
                return None
            if finding is not None:
                # CAS 成功后才是 winner：此时才校验 Finding 必须处于
                # under_review（并发 loser 的 item 已被 winner 改动，CAS
                # 已先行排除，不会把状态不匹配误报成 version conflict）。
                if finding.status != "under_review":
                    raise ApplicationError(
                        "finding review decision requires finding under_review",
                        code="review_finding_state_mismatch",
                    )
                finding.status = REVIEW_STATUS_TO_FINDING_STATUS[target_status]
                session.add(finding)
            session.add(decision)
            await session.commit()
            # CAS 成功后不信任此前加载的 item（Core UPDATE 未同步 identity map），
            # 重新以数据库值为准。
            await session.refresh(item)
            await session.refresh(decision)
            return item, decision

    async def submit_finding_for_review(
        self,
        *,
        case_id: str,
        finding_id: str,
        priority: int = 0,
        risk_level: str = "low",
        queue: str = "default",
        actor: str = "finding_submit_review",
    ) -> tuple[FindingRecord, ReviewItemRecord]:
        """Finding → Review 唯一原子提交入口（Post-Closure PC1 / RC1）。

        一个数据库事务内完成：锁定 Finding → 读取唯一 ReviewItem → 按
        PC1.3 状态行为表创建/复用/重新激活 → Finding.status=under_review →
        单次 commit。重复提交幂等；verified/rejected 复审复用既有 item；
        superseded 拒绝重新提交审核。

        RC1：generic Review API 与 Findings UI 共用此唯一入口。ReviewItem
        的 summary 一律使用 finding.statement（不信任客户端输入）；priority/
        risk_level/queue 首次创建时兼容 generic API，重复提交不覆盖既有
        metadata。
        """
        from sqlalchemy.exc import IntegrityError

        from app.services import review as review_domain

        async with self._database.session_factory() as session:
            finding = await session.scalar(
                select(FindingRecord)
                .where(FindingRecord.id == finding_id)
                .with_for_update()
            )
            if finding is None:
                raise ApplicationError(
                    f"finding '{finding_id}' does not exist",
                    code="finding_not_found",
                )
            if finding.case_id != case_id:
                raise ApplicationError(
                    "finding belongs to another case",
                    code="finding_scope_mismatch",
                )
            if finding.status == "superseded":
                raise ApplicationError(
                    "invalid finding transition superseded -> under_review",
                    code="finding_invalid_transition",
                )

            review_item = await session.scalar(
                select(ReviewItemRecord).where(
                    ReviewItemRecord.case_id == case_id,
                    ReviewItemRecord.object_type == "finding",
                    ReviewItemRecord.object_id == finding_id,
                )
            )

            state_changed = False
            if review_item is None:
                if finding.status in ("verified", "rejected"):
                    # 历史修复：已裁决 Finding 缺 ReviewItem → 直接 in_review
                    item_status = "in_review"
                else:
                    item_status = "unreviewed"
                review_item = ReviewItemRecord(
                    case_id=case_id,
                    object_type="finding",
                    object_id=finding_id,
                    summary=finding.statement,
                    priority=priority,
                    risk_level=risk_level,
                    queue=queue,
                    status=item_status,
                )
                session.add(review_item)
                state_changed = True
            elif finding.status == "under_review":
                if review_item.status in ("accepted", "rejected", "superseded"):
                    # 历史不一致恢复：Finding 已 under_review 但 item 已裁决
                    review_domain.validate_transition(
                        review_item.status, "in_review"
                    )
                    review_item.status = "in_review"
                    review_item.current_version += 1
                    state_changed = True
                # else: unreviewed/in_review/needs_more_evidence → 幂等返回
            elif finding.status in ("verified", "rejected"):
                # 复审：统一重新激活到 in_review（复用同一 item）
                if review_item.status != "in_review":
                    review_domain.validate_transition(
                        review_item.status, "in_review"
                    )
                    review_item.status = "in_review"
                    review_item.current_version += 1
                    state_changed = True
            else:  # candidate
                if review_item.status in (
                    "needs_more_evidence",
                    "accepted",
                    "rejected",
                    "superseded",
                ):
                    review_domain.validate_transition(
                        review_item.status, "in_review"
                    )
                    review_item.status = "in_review"
                    review_item.current_version += 1
                    state_changed = True
                # else: unreviewed/in_review → 复用

            if finding.status != "under_review":
                finding.status = "under_review"
                state_changed = True

            if state_changed:
                session.add(
                    CaseActivityLogRecord(
                        case_id=case_id,
                        activity_type="review_item_submitted",
                        summary=f"提交审核项：finding:{finding_id}",
                        actor=actor,
                        metadata_json={
                            "object_type": "finding",
                            "object_id": finding_id,
                        },
                    )
                )

            try:
                await session.commit()
            except IntegrityError:
                # 并发竞争：唯一约束兜底。rollback 后重读，若终态已满足
                # （Finding=under_review AND ReviewItem 存在）则幂等成功。
                await session.rollback()
                async with self._database.session_factory() as retry:
                    retry_finding = await retry.scalar(
                        select(FindingRecord)
                        .where(FindingRecord.id == finding_id)
                        .with_for_update()
                    )
                    retry_item = await retry.scalar(
                        select(ReviewItemRecord).where(
                            ReviewItemRecord.case_id == case_id,
                            ReviewItemRecord.object_type == "finding",
                            ReviewItemRecord.object_id == finding_id,
                        )
                    )
                    if (
                        retry_finding is not None
                        and retry_finding.status == "under_review"
                        and retry_item is not None
                    ):
                        return retry_finding, retry_item
                raise
            await session.refresh(finding)
            await session.refresh(review_item)
            return finding, review_item

    async def reopen_review_item_atomic(
        self,
        *,
        item_id: str,
        case_id: str | None = None,
    ) -> ReviewItemRecord:
        """Review Workbench 重开原子方法（PC2B / RC3）。

        一个事务内：锁定 ReviewItem → 校验 scope → domain 状态机校验 →
        若 object_type=finding 必须校验 Finding target 真实且同 Case，并
        校验 ReviewItem/Finding 状态配对，同步 Finding=under_review →
        ReviewItem.status=in_review → 单次 commit。非 Finding item 行为
        保持原样（只改 ReviewItem 状态，不访问 Finding 表）。

        RC3.2：superseded Finding 无法通过 Workbench reopen 复活；ReviewItem
        与 Finding 状态不匹配（如 accepted + candidate）一律 fail closed。
        """
        from app.services import review as review_domain

        async with self._database.session_factory() as session:
            item = await session.scalar(
                select(ReviewItemRecord)
                .where(ReviewItemRecord.id == item_id)
                .with_for_update()
            )
            if item is None:
                raise ResourceNotFoundError("review_item")
            if case_id is not None and item.case_id != case_id:
                raise ResourceNotFoundError("review_item")
            review_domain.validate_transition(item.status, "in_review")
            if item.object_type == "finding":
                finding = await self._require_finding_review_target(session, item)
                # RC3.2 状态配对：ReviewItem 与 Finding 必须同时满足各自状态机
                expected_finding = {
                    "accepted": "verified",
                    "rejected": "rejected",
                    "needs_more_evidence": "under_review",
                }
                if item.status in expected_finding and finding.status != expected_finding[
                    item.status
                ]:
                    raise ApplicationError(
                        "review finding state mismatch",
                        code="review_finding_state_mismatch",
                    )
                if finding.status == "superseded":
                    raise ApplicationError(
                        "superseded finding cannot be reopened",
                        code="review_finding_state_mismatch",
                    )
                finding.status = "under_review"
                session.add(finding)
            item.status = "in_review"
            item.current_version += 1
            await session.commit()
            await session.refresh(item)
            return item

    async def get_review_item_for_object(
        self, case_id: str, object_type: str, object_id: str
    ) -> dict[str, Any] | None:
        """返回对象的最新 Review item 摘要（Finding detail 聚合用）。"""
        async with self._database.session_factory() as session:
            record = (
                await session.scalars(
                    select(ReviewItemRecord)
                    .where(
                        ReviewItemRecord.case_id == case_id,
                        ReviewItemRecord.object_type == object_type,
                        ReviewItemRecord.object_id == object_id,
                    )
                    .order_by(ReviewItemRecord.created_at.desc())
                    .limit(1)
                )
            ).first()
            if record is None:
                return None
            return {
                "id": record.id,
                "status": record.status,
                "summary": record.summary,
                "updated_at": record.updated_at.isoformat() if record.updated_at else None,
            }

    async def release_review_item(
        self, item_id: str, actor: str
    ) -> ReviewItemRecord | None:
        # RH3: in_review → unreviewed 走条件 UPDATE（version+1），防止两个
        # 并发 release 都成功导致版本被覆盖为同一值。
        from sqlalchemy import update as sa_update

        async with self._database.session_factory() as session:
            assignment = await session.scalar(
                select(ReviewAssignmentRecord)
                .where(
                    ReviewAssignmentRecord.item_id == item_id,
                    ReviewAssignmentRecord.actor == actor,
                    ReviewAssignmentRecord.status == "active",
                )
                .order_by(ReviewAssignmentRecord.assigned_at.desc())
                .limit(1)
            )
            if assignment is None:
                return None
            result = await session.execute(
                sa_update(ReviewItemRecord)
                .where(
                    ReviewItemRecord.id == item_id,
                    ReviewItemRecord.status == "in_review",
                )
                .values(
                    status="unreviewed",
                    current_version=ReviewItemRecord.current_version + 1,
                    updated_at=datetime.now(UTC),
                )
                .execution_options(synchronize_session=False)
            )
            if int(result.rowcount or 0) != 1:
                await session.rollback()
                return None
            assignment.status = "released"
            session.add(assignment)
            await session.commit()
            record = await session.get(ReviewItemRecord, item_id)
            assert record is not None
            await session.refresh(record)
            return record

    async def add_review_decision(
        self, decision: ReviewDecisionRecord
    ) -> ReviewDecisionRecord:
        async with self._database.session_factory() as session:
            session.add(decision)
            await session.commit()
            await session.refresh(decision)
            return decision

    async def list_review_decisions(
        self, item_id: str, *, limit: int = 50
    ) -> Sequence[ReviewDecisionRecord]:
        query = (
            select(ReviewDecisionRecord)
            .where(ReviewDecisionRecord.item_id == item_id)
            .order_by(ReviewDecisionRecord.created_at.desc())
            .limit(limit)
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def add_review_comment(
        self, comment: ReviewCommentRecord
    ) -> ReviewCommentRecord:
        async with self._database.session_factory() as session:
            session.add(comment)
            await session.commit()
            await session.refresh(comment)
            return comment

    async def list_review_comments(
        self, item_id: str, *, limit: int = 100
    ) -> Sequence[ReviewCommentRecord]:
        query = (
            select(ReviewCommentRecord)
            .where(ReviewCommentRecord.item_id == item_id)
            .order_by(ReviewCommentRecord.created_at.asc())
            .limit(limit)
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def add_activity_log(
        self, log: CaseActivityLogRecord
    ) -> CaseActivityLogRecord:
        async with self._database.session_factory() as session:
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return log

    async def list_activity_log(
        self,
        case_id: str,
        *,
        activity_type: str | None = None,
        actor: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[CaseActivityLogRecord]:
        query = select(CaseActivityLogRecord).where(
            CaseActivityLogRecord.case_id == case_id
        )
        if activity_type:
            query = query.where(
                CaseActivityLogRecord.activity_type == activity_type
            )
        if actor:
            query = query.where(CaseActivityLogRecord.actor == actor)
        query = (
            query.order_by(
                CaseActivityLogRecord.created_at.desc(),
                CaseActivityLogRecord.id,
            )
            .limit(limit)
            .offset(offset)
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def list_review_policies(
        self, *, enabled: bool | None = True
    ) -> Sequence[ReviewPolicyRecord]:
        query = select(ReviewPolicyRecord)
        if enabled is not None:
            query = query.where(ReviewPolicyRecord.enabled == enabled)
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()
    # ---- 13 订阅 / 通知 / 分享 / 导出 --------------------------------------

    async def create_subscription(
        self, subscription: SubscriptionRecord
    ) -> SubscriptionRecord:
        async with self._database.session_factory() as session:
            session.add(subscription)
            await session.commit()
            await session.refresh(subscription)
            return subscription

    async def list_subscriptions(
        self, case_id: str
    ) -> Sequence[SubscriptionRecord]:
        query = (
            select(SubscriptionRecord)
            .where(SubscriptionRecord.case_id == case_id)
            .order_by(SubscriptionRecord.created_at.desc())
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def set_subscription_enabled(
        self, case_id: str, subscription_id: str, enabled: bool
    ) -> SubscriptionRecord:
        async with self._database.session_factory() as session:
            record = await session.get(SubscriptionRecord, subscription_id)
            if record is None or record.case_id != case_id:
                raise ResourceNotFoundError("subscription")
            record.enabled = enabled
            record.version += 1
            await session.commit()
            await session.refresh(record)
            return record

    async def create_endpoint(
        self, endpoint: NotificationEndpointRecord
    ) -> NotificationEndpointRecord:
        async with self._database.session_factory() as session:
            session.add(endpoint)
            await session.commit()
            await session.refresh(endpoint)
            return endpoint

    async def get_endpoint(self, endpoint_id: str) -> NotificationEndpointRecord:
        async with self._database.session_factory() as session:
            record = await session.get(NotificationEndpointRecord, endpoint_id)
        if record is None:
            raise ResourceNotFoundError("notification_endpoint", endpoint_id)
        return record

    async def set_endpoint_verification(
        self, endpoint_id: str, state: str
    ) -> NotificationEndpointRecord:
        async with self._database.session_factory() as session:
            record = await session.get(NotificationEndpointRecord, endpoint_id)
            if record is None:
                raise ResourceNotFoundError("notification_endpoint", endpoint_id)
            record.verification_state = state
            await session.commit()
            await session.refresh(record)
            return record

    async def list_endpoints(
        self, case_id: str
    ) -> Sequence[NotificationEndpointRecord]:
        query = (
            select(NotificationEndpointRecord)
            .where(NotificationEndpointRecord.case_id == case_id)
            .order_by(NotificationEndpointRecord.created_at.desc())
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def enqueue_notification_event(
        self, event: NotificationEventRecord
    ) -> NotificationEventRecord:
        async with self._database.session_factory() as session:
            session.add(event)
            try:
                await session.commit()
            except Exception:
                # dedupe_key 冲突 = 幂等，返回已有事件。
                await session.rollback()
                existing = await session.scalar(
                    select(NotificationEventRecord).where(
                        NotificationEventRecord.dedupe_key == event.dedupe_key
                    )
                )
                if existing is not None:
                    return existing
                raise
            await session.refresh(event)
            return event

    async def list_undelivered_events(
        self, *, limit: int = 20
    ) -> Sequence[NotificationEventRecord]:
        query = (
            select(NotificationEventRecord)
            .where(NotificationEventRecord.delivered == False)  # noqa: E712
            .order_by(NotificationEventRecord.occurred_at.asc())
            .limit(limit)
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def list_notification_events(
        self, case_id: str, *, limit: int = 100
    ) -> Sequence[NotificationEventRecord]:
        query = (
            select(NotificationEventRecord)
            .where(NotificationEventRecord.case_id == case_id)
            .order_by(NotificationEventRecord.occurred_at.desc())
            .limit(limit)
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def reset_delivery_for_retry(
        self, case_id: str, delivery_id: str
    ) -> bool:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(DeliveryAttemptRecord)
                .join(
                    NotificationEventRecord,
                    NotificationEventRecord.event_id
                    == DeliveryAttemptRecord.event_id,
                )
                .where(
                    DeliveryAttemptRecord.id == delivery_id,
                    NotificationEventRecord.case_id == case_id,
                )
            )
            if record is None:
                return False
            record.status = "pending"
            record.next_retry_at = None
            record.error_code = None
            await session.commit()
            return True

    async def mark_delivered(
        self, events: Sequence[NotificationEventRecord]
    ) -> None:
        if not events:
            return
        ids = [e.id for e in events]
        async with self._database.session_factory() as session:
            records = (
                await session.scalars(
                    select(NotificationEventRecord).where(
                        NotificationEventRecord.id.in_(ids)
                    )
                )
            ).all()
            for record in records:
                record.delivered = True
            await session.commit()

    async def get_or_create_delivery(
        self, delivery: DeliveryAttemptRecord
    ) -> DeliveryAttemptRecord | None:
        async with self._database.session_factory() as session:
            existing = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.event_id == delivery.event_id,
                    DeliveryAttemptRecord.subscription_id
                    == delivery.subscription_id,
                )
            )
            if existing is not None:
                return existing
            session.add(delivery)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                existing = await session.scalar(
                    select(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.event_id == delivery.event_id,
                        DeliveryAttemptRecord.subscription_id
                        == delivery.subscription_id,
                    )
                )
                return existing
            await session.refresh(delivery)
            return delivery

    async def update_delivery_status(
        self,
        delivery_id: str,
        status: str,
        *,
        http_status: int | None = None,
        http_summary: str = "",
        duration_ms: int = 0,
        next_retry_at: datetime | None = None,
        error_code: str | None = None,
        attempt: int | None = None,
    ) -> None:
        async with self._database.session_factory() as session:
            record = await session.get(DeliveryAttemptRecord, delivery_id)
            if record is None:
                return
            record.status = status
            if http_status is not None:
                record.http_status = http_status
            record.http_summary = http_summary
            record.duration_ms = duration_ms
            record.next_retry_at = next_retry_at
            record.error_code = error_code
            if attempt is not None:
                record.attempt = attempt
            await session.commit()

    async def list_deliveries(
        self, case_id: str, *, limit: int = 100
    ) -> Sequence[DeliveryAttemptRecord]:
        query = (
            select(DeliveryAttemptRecord)
            .join(
                NotificationEventRecord,
                NotificationEventRecord.event_id
                == DeliveryAttemptRecord.event_id,
            )
            .where(NotificationEventRecord.case_id == case_id)
            .order_by(DeliveryAttemptRecord.created_at.desc())
            .limit(limit)
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def create_share_link(
        self, link: ShareLinkRecord
    ) -> ShareLinkRecord:
        async with self._database.session_factory() as session:
            session.add(link)
            await session.commit()
            await session.refresh(link)
            return link

    async def get_share_link_by_hash(
        self, token_hash: str
    ) -> ShareLinkRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(ShareLinkRecord).where(
                    ShareLinkRecord.token_hash == token_hash
                )
            )

    async def consume_share_download(self, link_id: str, *, per_minute: int, now: datetime) -> bool:
        """原子消费总下载配额和一分钟窗口配额。"""
        from sqlalchemy import case
        from sqlalchemy import update as sa_update

        cutoff = now - timedelta(minutes=1)
        reset_window = or_(
            ShareLinkRecord.download_window_started_at.is_(None),
            ShareLinkRecord.download_window_started_at <= cutoff,
        )
        async with self._database.session_factory() as session:
            result = await session.execute(
                sa_update(ShareLinkRecord)
                .where(
                    ShareLinkRecord.id == link_id,
                    ShareLinkRecord.revoked_at.is_(None),
                    or_(ShareLinkRecord.expires_at.is_(None), ShareLinkRecord.expires_at >= now),
                    or_(
                        ShareLinkRecord.download_limit <= 0,
                        ShareLinkRecord.download_count < ShareLinkRecord.download_limit,
                    ),
                    or_(reset_window, ShareLinkRecord.download_window_count < per_minute),
                )
                .values(
                    download_count=ShareLinkRecord.download_count + 1,
                    download_window_started_at=case(
                        (reset_window, now), else_=ShareLinkRecord.download_window_started_at
                    ),
                    download_window_count=case(
                        (reset_window, 1), else_=ShareLinkRecord.download_window_count + 1
                    ),
                )
            )
            await session.commit()
            return int(result.rowcount or 0) == 1

    async def revoke_share_link(self, link_id: str) -> None:
        async with self._database.session_factory() as session:
            record = await session.get(ShareLinkRecord, link_id)
            if record is not None:
                record.revoked_at = datetime.now(UTC)
                await session.commit()

    async def create_export_job(
        self, job: ExportJobRecord
    ) -> ExportJobRecord:
        async with self._database.session_factory() as session:
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job

    async def list_export_jobs(
        self, case_id: str
    ) -> Sequence[ExportJobRecord]:
        query = (
            select(ExportJobRecord)
            .where(ExportJobRecord.case_id == case_id)
            .order_by(ExportJobRecord.created_at.desc())
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(query)
            return result.all()

    async def get_export_job(self, job_id: str) -> ExportJobRecord:
        async with self._database.session_factory() as session:
            record = await session.get(ExportJobRecord, job_id)
        if record is None:
            from app.core.errors import ResourceNotFoundError

            raise ResourceNotFoundError("export_job")
        return record

    # ------------------------------------------------------------------
    # M16: 内容安全评估与护栏决策
    # ------------------------------------------------------------------

    async def add_content_security_assessment(
        self,
        *,
        object_type: str,
        object_id: str,
        run_id: str | None,
        trust_level: str,
        classification: str,
        score: float,
        risk_signals: list[dict[str, object]],
        detector: str,
        detector_version: str,
        disposition: str,
        reason: str,
        content_hash: str,
        source_type: str,
        review_state: str,
    ) -> ContentSecurityAssessmentRecord:
        record = ContentSecurityAssessmentRecord(
            object_type=object_type,
            object_id=object_id,
            run_id=run_id,
            trust_level=trust_level,
            classification=classification,
            score=score,
            risk_signals=risk_signals,
            detector=detector,
            detector_version=detector_version,
            disposition=disposition,
            reason=reason,
            content_hash=content_hash,
            source_type=source_type,
            review_state=review_state,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_content_security_assessments(
        self,
        *,
        run_id: str | None = None,
        trust_level: str | None = None,
        disposition: str | None = None,
        limit: int = 100,
    ) -> Sequence[ContentSecurityAssessmentRecord]:
        stmt = select(ContentSecurityAssessmentRecord).order_by(
            ContentSecurityAssessmentRecord.created_at.desc()
        )
        if run_id:
            stmt = stmt.where(ContentSecurityAssessmentRecord.run_id == run_id)
        if trust_level:
            stmt = stmt.where(
                ContentSecurityAssessmentRecord.trust_level == trust_level
            )
        if disposition:
            stmt = stmt.where(
                ContentSecurityAssessmentRecord.disposition == disposition
            )
        async with self._database.session_factory() as session:
            result = await session.scalars(stmt.limit(limit))
            return result.all()

    async def add_guardrail_decision(
        self,
        *,
        stage: str,
        run_id: str | None,
        turn_id: str | None,
        tool_call_id: str | None,
        tool: str | None,
        decision: str,
        reason: str,
        policy_version: str,
        signal_ids: list[str],
        content_hash: str,
        summary: str,
    ) -> GuardrailDecisionRecord:
        record = GuardrailDecisionRecord(
            stage=stage,
            run_id=run_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            tool=tool,
            decision=decision,
            reason=reason,
            policy_version=policy_version,
            signal_ids=signal_ids,
            content_hash=content_hash,
            summary=summary,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_guardrail_decisions(
        self,
        *,
        run_id: str | None = None,
        stage: str | None = None,
        decision: str | None = None,
        limit: int = 100,
    ) -> Sequence[GuardrailDecisionRecord]:
        stmt = select(GuardrailDecisionRecord).order_by(
            GuardrailDecisionRecord.created_at.desc()
        )
        if run_id:
            stmt = stmt.where(GuardrailDecisionRecord.run_id == run_id)
        if stage:
            stmt = stmt.where(GuardrailDecisionRecord.stage == stage)
        if decision:
            stmt = stmt.where(GuardrailDecisionRecord.decision == decision)
        async with self._database.session_factory() as session:
            result = await session.scalars(stmt.limit(limit))
            return result.all()

    async def content_security_summary(
        self,
    ) -> dict[str, object]:
        """按处置/信任分组的摘要统计（供安全事件视图）。"""
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(
                    ContentSecurityAssessmentRecord.disposition,
                    ContentSecurityAssessmentRecord.trust_level,
                    ContentSecurityAssessmentRecord.object_type,
                )
            )
        by_disposition: dict[str, int] = {}
        by_trust: dict[str, int] = {}
        by_object_type: dict[str, int] = {}
        for disposition, trust_level, object_type in rows:
            by_disposition[disposition] = by_disposition.get(disposition, 0) + 1
            by_trust[trust_level] = by_trust.get(trust_level, 0) + 1
            by_object_type[object_type] = by_object_type.get(object_type, 0) + 1
        return {
            "by_disposition": by_disposition,
            "by_trust_level": by_trust,
            "by_object_type": by_object_type,
        }

    # ------------------------------------------------------------------
    # M17: 显式目标、计划图与完成条件
    # ------------------------------------------------------------------

    async def create_goal(
        self,
        *,
        case_id: str,
        title: str,
        objective: str,
        scope: dict[str, object] | None = None,
        constraints: list[str] | None = None,
        priority: str = "normal",
        source: str = "user",
    ) -> GoalRecord:
        await self.get_case(case_id)
        record = GoalRecord(
            case_id=case_id,
            title=title,
            objective=objective,
            scope=scope or {},
            constraints=constraints or [],
            priority=priority,
            status="draft",
            version=1,
            source=source,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_goal(self, goal_id: str) -> GoalRecord:
        async with self._database.session_factory() as session:
            record = await session.get(GoalRecord, goal_id)
            if record is None:
                raise ResourceNotFoundError("goal", goal_id)
            return record

    async def list_goals(self, case_id: str) -> Sequence[GoalRecord]:
        await self.get_case(case_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(GoalRecord)
                .where(GoalRecord.case_id == case_id)
                .order_by(GoalRecord.created_at.desc())
            )
            return result.all()

    async def update_goal_status(
        self,
        goal_id: str,
        *,
        status: str,
        cancelled_reason: str | None = None,
    ) -> GoalRecord:
        await self.get_goal(goal_id)
        async with self._database.session_factory() as session:
            current = await session.get(GoalRecord, goal_id)
            assert current is not None
            current.status = status
            if cancelled_reason is not None:
                current.cancelled_reason = cancelled_reason
            await session.commit()
            await session.refresh(current)
        return current

    async def add_acceptance_criteria(
        self,
        goal_id: str,
        criteria: list[dict[str, object]],
    ) -> list[AcceptanceCriterionRecord]:
        await self.get_goal(goal_id)
        records: list[AcceptanceCriterionRecord] = []
        async with self._database.session_factory() as session:
            for item in criteria:
                record = AcceptanceCriterionRecord(
                    goal_id=goal_id,
                    criterion_type=str(item.get("criterion_type") or "artifact_exists"),
                    description=str(item.get("description") or ""),
                    target=dict(item.get("target") or {}),
                    evidence_requirement=str(
                        item.get("evidence_requirement") or "required"
                    ),
                    status="pending",
                    required=bool(item.get("required", True)),
                )
                session.add(record)
                records.append(record)
            await session.commit()
            for record in records:
                await session.refresh(record)
        return records

    async def list_acceptance_criteria(
        self,
        goal_id: str,
    ) -> Sequence[AcceptanceCriterionRecord]:
        await self.get_goal(goal_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(AcceptanceCriterionRecord)
                .where(AcceptanceCriterionRecord.goal_id == goal_id)
                .order_by(AcceptanceCriterionRecord.created_at.asc())
            )
            return result.all()

    async def update_criterion_status(
        self,
        criterion_id: str,
        status: str,
    ) -> AcceptanceCriterionRecord:
        async with self._database.session_factory() as session:
            current = await session.get(AcceptanceCriterionRecord, criterion_id)
            if current is None:
                raise ResourceNotFoundError("acceptance_criterion", criterion_id)
            current.status = status
            await session.commit()
            await session.refresh(current)
        return current

    async def create_plan_version(
        self,
        *,
        goal_id: str,
        version: int = 1,
        planner: str = "deterministic",
    ) -> PlanVersionRecord:
        await self.get_goal(goal_id)
        record = PlanVersionRecord(
            goal_id=goal_id,
            version=version,
            status="draft",
            planner=planner,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_plan_version(self, plan_version_id: str) -> PlanVersionRecord:
        async with self._database.session_factory() as session:
            record = await session.get(PlanVersionRecord, plan_version_id)
            if record is None:
                raise ResourceNotFoundError("plan_version", plan_version_id)
            return record

    async def list_plan_versions(
        self,
        goal_id: str,
    ) -> Sequence[PlanVersionRecord]:
        await self.get_goal(goal_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(PlanVersionRecord)
                .where(PlanVersionRecord.goal_id == goal_id)
                .order_by(PlanVersionRecord.version.desc())
            )
            return result.all()

    async def update_plan_version_status(
        self,
        plan_version_id: str,
        *,
        status: str,
        frozen_at=None,
    ) -> PlanVersionRecord:
        await self.get_plan_version(plan_version_id)
        async with self._database.session_factory() as session:
            current = await session.get(PlanVersionRecord, plan_version_id)
            assert current is not None
            current.status = status
            if frozen_at is not None:
                current.frozen_at = frozen_at
            await session.commit()
            await session.refresh(current)
        return current

    async def add_plan_step(
        self,
        *,
        plan_version_id: str,
        step_key: str,
        task: str,
        agent_capability: str,
        budget_max_cost: float = 5.0,
        max_turns: int = 16,
        max_retries: int = 0,
        declared_by: str = "planner",
    ) -> PlanStepRecord:
        await self.get_plan_version(plan_version_id)
        record = PlanStepRecord(
            plan_version_id=plan_version_id,
            step_key=step_key,
            task=task,
            agent_capability=agent_capability,
            status="pending",
            budget_max_cost=budget_max_cost,
            max_turns=max_turns,
            max_retries=max_retries,
            declared_by=declared_by,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def add_plan_step_batch(
        self,
        *,
        plan_version_id: str,
        steps: list[dict[str, object]],
        declared_by: str = "planner",
    ) -> list[PlanStepRecord]:
        await self.get_plan_version(plan_version_id)
        records: list[PlanStepRecord] = []
        async with self._database.session_factory() as session:
            for item in steps:
                record = PlanStepRecord(
                    plan_version_id=plan_version_id,
                    step_key=str(item.get("step_key") or ""),
                    task=str(item.get("task") or ""),
                    agent_capability=str(
                        item.get("agent_capability") or "coordinator"
                    ),
                    status="pending",
                    budget_max_cost=float(item.get("budget_max_cost") or 5.0),
                    max_turns=int(item.get("max_turns") or 16),
                    max_retries=int(item.get("max_retries") or 0),
                    declared_by=declared_by,
                )
                session.add(record)
                records.append(record)
            await session.commit()
            for record in records:
                await session.refresh(record)
        return records

    async def list_plan_steps(
        self,
        plan_version_id: str,
    ) -> Sequence[PlanStepRecord]:
        await self.get_plan_version(plan_version_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(PlanStepRecord)
                .where(PlanStepRecord.plan_version_id == plan_version_id)
                .order_by(PlanStepRecord.created_at.asc())
            )
            return result.all()

    async def get_plan_step(self, step_id: str) -> PlanStepRecord:
        async with self._database.session_factory() as session:
            record = await session.get(PlanStepRecord, step_id)
            if record is None:
                raise ResourceNotFoundError("plan_step", step_id)
            return record

    async def update_plan_step(
        self,
        step_id: str,
        *,
        status: str | None = None,
        run_id: str | None = None,
        retry_count: int | None = None,
        completion_declared_by: str | None = None,
    ) -> PlanStepRecord:
        await self.get_plan_step(step_id)
        async with self._database.session_factory() as session:
            current = await session.get(PlanStepRecord, step_id)
            assert current is not None
            if status is not None:
                current.status = status
            if run_id is not None:
                current.run_id = run_id
            if retry_count is not None:
                current.retry_count = retry_count
            if completion_declared_by is not None:
                current.completion_declared_by = completion_declared_by
            await session.commit()
            await session.refresh(current)
        return current

    async def add_plan_edge(
        self,
        *,
        plan_version_id: str,
        source_step_key: str,
        target_step_key: str,
        edge_type: str = "dependency",
    ) -> PlanEdgeRecord:
        await self.get_plan_version(plan_version_id)
        record = PlanEdgeRecord(
            plan_version_id=plan_version_id,
            source_step_key=source_step_key,
            target_step_key=target_step_key,
            edge_type=edge_type,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            await session.refresh(record)
        return record

    async def list_plan_edges(
        self,
        plan_version_id: str,
    ) -> Sequence[PlanEdgeRecord]:
        await self.get_plan_version(plan_version_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(PlanEdgeRecord)
                .where(PlanEdgeRecord.plan_version_id == plan_version_id)
                .order_by(PlanEdgeRecord.created_at.asc())
            )
            return result.all()

    async def add_step_evidence(
        self,
        *,
        step_id: str,
        evidence_type: str,
        ref_id: str,
        ref_kind: str,
        payload: dict[str, object] | None = None,
    ) -> StepEvidenceRecord:
        await self.get_plan_step(step_id)
        record = StepEvidenceRecord(
            step_id=step_id,
            evidence_type=evidence_type,
            ref_id=ref_id,
            ref_kind=ref_kind,
            payload=payload or {},
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_step_evidence(
        self,
        step_id: str,
    ) -> Sequence[StepEvidenceRecord]:
        await self.get_plan_step(step_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(StepEvidenceRecord)
                .where(StepEvidenceRecord.step_id == step_id)
                .order_by(StepEvidenceRecord.created_at.asc())
            )
            return result.all()

    async def create_completion_assessment(
        self,
        *,
        goal_id: str,
        plan_version_id: str,
        verifier: str,
        result: str,
        criterion_results: dict[str, object],
        gaps: list[str],
    ) -> CompletionAssessmentRecord:
        """幂等写入：同一 goal+plan 版本只保留一次评估（重复调用更新）。"""
        await self.get_goal(goal_id)
        await self.get_plan_version(plan_version_id)
        async with self._database.session_factory() as session:
            existing = await session.scalar(
                select(CompletionAssessmentRecord).where(
                    CompletionAssessmentRecord.goal_id == goal_id,
                    CompletionAssessmentRecord.plan_version_id == plan_version_id,
                )
            )
            if existing is not None:
                existing.verifier = verifier
                existing.result = result
                existing.criterion_results = criterion_results
                existing.gaps = gaps
                record = existing
            else:
                record = CompletionAssessmentRecord(
                    goal_id=goal_id,
                    plan_version_id=plan_version_id,
                    verifier=verifier,
                    result=result,
                    criterion_results=criterion_results,
                    gaps=gaps,
                )
                session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_completion_assessment(
        self,
        goal_id: str,
        plan_version_id: str,
    ) -> CompletionAssessmentRecord | None:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(CompletionAssessmentRecord).where(
                    CompletionAssessmentRecord.goal_id == goal_id,
                    CompletionAssessmentRecord.plan_version_id == plan_version_id,
                )
            )
            return record

    async def list_completion_assessments(
        self,
        goal_id: str,
    ) -> Sequence[CompletionAssessmentRecord]:
        await self.get_goal(goal_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(CompletionAssessmentRecord)
                .where(CompletionAssessmentRecord.goal_id == goal_id)
                .order_by(CompletionAssessmentRecord.created_at.desc())
            )
            return result.all()

    # ------------------------------------------------------------------
    # M21: 广义人工介入——审批收件箱、过期与一次性执行授权
    # ------------------------------------------------------------------

    async def list_approvals(
        self,
        *,
        case_id: str | None = None,
        run_id: str | None = None,
        status: str | None = None,
        approval_type: str | None = None,
        risk_level: str | None = None,
        limit: int = 100,
    ) -> Sequence[ApprovalRecord]:
        stmt = select(ApprovalRecord).order_by(
            ApprovalRecord.created_at.desc()
        )
        if run_id:
            stmt = stmt.where(ApprovalRecord.run_id == run_id)
        if status:
            stmt = stmt.where(ApprovalRecord.status == status)
        if approval_type:
            stmt = stmt.where(ApprovalRecord.approval_type == approval_type)
        if risk_level:
            stmt = stmt.where(ApprovalRecord.risk_level == risk_level)
        if case_id:
            stmt = stmt.join(
                AgentRunRecord,
                AgentRunRecord.id == ApprovalRecord.run_id,
            ).where(AgentRunRecord.case_id == case_id)
        async with self._database.session_factory() as session:
            result = await session.scalars(stmt.limit(limit))
            return result.all()

    async def update_approval_full(
        self,
        approval_id: str,
        *,
        status: str | None = None,
        decision: str | None = None,
        decision_payload: dict[str, object] | None = None,
        edited_action: dict[str, object] | None = None,
        actor: str | None = None,
        decision_version: str | None = None,
        supersedes_id: str | None = None,
        expires_at=None,
    ) -> ApprovalRecord:
        await self.get_approval(approval_id)
        async with self._database.session_factory() as session:
            current = await session.get(ApprovalRecord, approval_id)
            assert current is not None
            # M21: 终态不可改写（过期/拒绝/取消/消费后不能再决策）。
            if (
                status is not None
                and current.status != "pending"
                and status != current.status
            ):
                raise ValueError(
                    "Approval is in terminal state: " + current.status
                )
            if status is not None:
                current.status = status
            if decision is not None:
                current.decision = decision
            if decision_payload is not None:
                current.decision_payload = decision_payload
            if edited_action is not None:
                current.edited_action = edited_action
            if actor is not None:
                current.actor = actor
            if decision_version is not None:
                current.decision_version = decision_version
            if supersedes_id is not None:
                current.supersedes_id = supersedes_id
            if expires_at is not None:
                current.expires_at = expires_at
            if status in {"approved", "approved_with_edits", "rejected"}:
                current.decided_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(current)
        return current

    async def expire_pending_approvals(
        self,
        now=None,
    ) -> int:
        """把过期的 pending 审批标记 expired（历史保留，不物理删除）。"""
        from sqlalchemy import update as sa_update

        now = now or datetime.now(UTC)
        async with self._database.session_factory() as session:
            result = await session.execute(
                sa_update(ApprovalRecord)
                .where(
                    ApprovalRecord.status == "pending",
                    ApprovalRecord.expires_at.is_not(None),
                    ApprovalRecord.expires_at < now,
                )
                .values(status="expired"),
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def get_approval_statistics(
        self,
    ) -> dict[str, object]:
        """审批率/编辑率/拒绝率/过期率与平均等待时长（脱敏聚合）。"""
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(ApprovalRecord.status, ApprovalRecord.decision)
            )
        total = 0
        decided = 0
        approved = 0
        edited = 0
        rejected = 0
        expired = 0
        cancelled = 0
        for status, _decision in rows:
            total += 1
            if status == "expired":
                expired += 1
            elif status == "cancelled":
                cancelled += 1
            if status in {"approved", "approved_with_edits", "rejected"}:
                decided += 1
                if status == "approved":
                    approved += 1
                elif status == "approved_with_edits":
                    edited += 1
                elif status == "rejected":
                    rejected += 1
        return {
            "total": total,
            "decided": decided,
            "approved": approved,
            "approved_with_edits": edited,
            "rejected": rejected,
            "expired": expired,
            "cancelled": cancelled,
            "approval_rate": round(approved / decided, 4) if decided else 0.0,
            "edit_rate": round(edited / decided, 4) if decided else 0.0,
            "rejection_rate": round(rejected / decided, 4) if decided else 0.0,
            "expiry_rate": round(expired / total, 4) if total else 0.0,
        }

    # -- 一次性执行授权 ----------------------------------------------------
    # M21/M22 一次性消费：一个审批至多一条授权（approval_id 唯一），
    # 消费绑定 action_family + resource_id + 参数哈希 + 期限；消费后
    # 失效，重复使用同一审批（工具调用 / Kill Switch / 死信重试）会被拒绝。

    async def create_execution_authorization(
        self,
        *,
        approval_id: str,
        run_id: str,
        tool_name: str,
        argument_hash: str,
        token_hash: str,
        action_family: str = "",
        resource_id: str = "",
        expires_at=None,
    ) -> ExecutionAuthorizationRecord:
        await self.get_approval(approval_id)
        record = ExecutionAuthorizationRecord(
            approval_id=approval_id,
            run_id=run_id,
            tool_name=tool_name,
            argument_hash=argument_hash,
            token_hash=token_hash,
            action_family=action_family,
            resource_id=resource_id,
            expires_at=expires_at,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except Exception:
                # 唯一约束冲突：同一审批已绑定过执行授权。
                raise ApplicationError(
                    f"approval {approval_id} already issued an execution authorization",
                    code="authorization_already_issued",
                ) from None
            await session.refresh(record)
        return record

    async def get_execution_authorization_by_approval(
        self,
        approval_id: str,
    ) -> ExecutionAuthorizationRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(ExecutionAuthorizationRecord).where(
                    ExecutionAuthorizationRecord.approval_id == approval_id
                )
            )

    async def consume_execution_authorization(
        self,
        *,
        token_hash: str,
        run_id: str,
        tool_name: str,
        argument_hash: str,
        now=None,
    ) -> bool:
        """原子消费：绑定 tool+参数哈希+run+期限，消费后失效。"""
        from sqlalchemy import update as sa_update

        now = now or datetime.now(UTC)
        async with self._database.session_factory() as session:
            result = await session.execute(
                sa_update(ExecutionAuthorizationRecord)
                .where(
                    ExecutionAuthorizationRecord.token_hash == token_hash,
                    ExecutionAuthorizationRecord.run_id == run_id,
                    ExecutionAuthorizationRecord.tool_name == tool_name,
                    ExecutionAuthorizationRecord.argument_hash == argument_hash,
                    ExecutionAuthorizationRecord.consumed_at.is_(None),
                    or_(
                        ExecutionAuthorizationRecord.expires_at.is_(None),
                        ExecutionAuthorizationRecord.expires_at >= now,
                    ),
                )
                .values(consumed_at=now)
            )
            await session.commit()
            return int(result.rowcount or 0) == 1

    async def consume_authorization_by_approval(
        self,
        *,
        approval_id: str,
        action_family: str,
        resource_id: str,
        argument_hash: str,
        now=None,
    ) -> bool:
        """按 approval_id 原子消费（不依赖 token 明文）：审批对象 + 操作族 +
        资源 ID + 参数哈希 + 有效期全部匹配且未消费时，才允许通过。"""
        now = now or datetime.now(UTC)
        async with self._database.session_factory() as session:
            rows = await self.consume_authorization_in_session(
                session,
                approval_id=approval_id,
                action_family=action_family,
                resource_id=resource_id,
                argument_hash=argument_hash,
                now=now,
            )
            await session.commit()
            return rows == 1

    @staticmethod
    async def consume_authorization_in_session(
        session: Any,
        *,
        approval_id: str,
        action_family: str,
        resource_id: str,
        argument_hash: str,
        now=None,
    ) -> int:
        """事务内原子消费（供业务仓储在同一个 session 中与业务变更合并）。"""
        from sqlalchemy import update as sa_update

        now = now or datetime.now(UTC)
        result = await session.execute(
            sa_update(ExecutionAuthorizationRecord)
            .where(
                ExecutionAuthorizationRecord.approval_id == approval_id,
                ExecutionAuthorizationRecord.action_family == action_family,
                ExecutionAuthorizationRecord.resource_id == resource_id,
                ExecutionAuthorizationRecord.argument_hash == argument_hash,
                ExecutionAuthorizationRecord.consumed_at.is_(None),
                or_(
                    ExecutionAuthorizationRecord.expires_at.is_(None),
                    ExecutionAuthorizationRecord.expires_at >= now,
                ),
            )
            .values(consumed_at=now)
        )
        return int(result.rowcount or 0)

    # ------------------------------------------------------------------
    # M20: 评测数据集、运行与发布门禁
    # ------------------------------------------------------------------

    async def create_dataset_manifest(
        self,
        manifest: dict[str, object],
        content_hash_value: str,
    ) -> DatasetManifestRecord:
        record = DatasetManifestRecord(
            name=str(manifest.get("name") or ""),
            version=str(manifest.get("version") or "1.0.0"),
            task=str(manifest.get("task") or ""),
            source=str(manifest.get("source") or ""),
            license=str(manifest.get("license") or ""),
            time_range=dict(manifest.get("time_range") or {}),
            platforms=list(manifest.get("platforms") or []),
            schema_version=str(manifest.get("schema_version") or "1.0"),
            content_hash=content_hash_value,
            train_holdout=bool(manifest.get("train_holdout") or False),
            example_count=int(manifest.get("example_count") or 0),
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_dataset_manifest(
        self, manifest_id: str
    ) -> DatasetManifestRecord:
        async with self._database.session_factory() as session:
            record = await session.get(DatasetManifestRecord, manifest_id)
            if record is None:
                raise ResourceNotFoundError("dataset_manifest", manifest_id)
            return record

    async def list_dataset_manifests(
        self, limit: int = 50
    ) -> Sequence[DatasetManifestRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(DatasetManifestRecord)
                .order_by(DatasetManifestRecord.created_at.desc())
                .limit(limit),
            )
            return result.all()

    async def add_dataset_examples(
        self,
        manifest_id: str,
        examples: list[dict[str, object]],
    ) -> int:
        await self.get_dataset_manifest(manifest_id)
        async with self._database.session_factory() as session:
            for example in examples:
                session.add(
                    DatasetExampleRecord(
                        manifest_id=manifest_id,
                        example_id=str(example.get("example_id") or ""),
                        task=str(example.get("task") or ""),
                        input_ref=str(example.get("input_ref") or ""),
                        input_hash=str(example.get("input_hash") or ""),
                        gold=example.get("gold"),
                        difficulty=str(example.get("difficulty") or "normal"),
                        label_disagreement=bool(
                            example.get("label_disagreement") or False
                        ),
                        training_blocked=bool(
                            example.get("training_blocked") or False
                        ),
                    )
                )
            await session.commit()
        return len(examples)

    async def list_dataset_examples(
        self, manifest_id: str, limit: int | None = None
    ) -> Sequence[DatasetExampleRecord]:
        await self.get_dataset_manifest(manifest_id)
        async with self._database.session_factory() as session:
            statement = select(DatasetExampleRecord).where(
                DatasetExampleRecord.manifest_id == manifest_id
            )
            if limit is not None:
                statement = statement.limit(limit)
            result = await session.scalars(statement)
            return result.all()

    async def create_evaluation_run(
        self,
        *,
        suite: str,
        candidate_version: str,
        baseline_version: str,
        dataset_manifest_id: str,
        commit: str = "",
        config: dict[str, object] | None = None,
    ) -> EvaluationRunRecord:
        await self.get_dataset_manifest(dataset_manifest_id)
        record = EvaluationRunRecord(
            suite=suite,
            candidate_version=candidate_version,
            baseline_version=baseline_version,
            dataset_manifest_id=dataset_manifest_id,
            commit=commit,
            config=config or {},
            status="running",
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def finish_evaluation_run(
        self,
        run_id: str,
        *,
        status: str,
        results: dict[str, object],
        aggregate: dict[str, object],
        differences: list[dict[str, object]],
        error_samples: list[dict[str, object]],
    ) -> EvaluationRunRecord:
        async with self._database.session_factory() as session:
            current = await session.get(EvaluationRunRecord, run_id)
            if current is None:
                raise ResourceNotFoundError("evaluation_run", run_id)
            current.status = status
            current.results = results
            current.aggregate = aggregate
            current.differences = differences
            current.error_samples = error_samples
            current.finished_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(current)
        return current

    async def get_evaluation_run(
        self, run_id: str
    ) -> EvaluationRunRecord:
        async with self._database.session_factory() as session:
            record = await session.get(EvaluationRunRecord, run_id)
            if record is None:
                raise ResourceNotFoundError("evaluation_run", run_id)
            return record

    async def list_evaluation_runs(
        self, suite: str | None = None, limit: int = 50
    ) -> Sequence[EvaluationRunRecord]:
        stmt = select(EvaluationRunRecord).order_by(
            EvaluationRunRecord.created_at.desc()
        )
        if suite:
            stmt = stmt.where(EvaluationRunRecord.suite == suite)
        async with self._database.session_factory() as session:
            result = await session.scalars(stmt.limit(limit))
            return result.all()

    async def create_release_gate(
        self, gate: dict[str, object]
    ) -> ReleaseGateRecord:
        record = ReleaseGateRecord(
            name=str(gate.get("name") or ""),
            suite=str(gate.get("suite") or "default"),
            thresholds=dict(gate.get("thresholds") or {}),
            relative_regression_limits=dict(
                gate.get("relative_regression_limits") or {}
            ),
            mandatory=bool(gate.get("mandatory", True)),
            enabled=bool(gate.get("enabled", True)),
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_release_gates(
        self, suite: str | None = None
    ) -> Sequence[ReleaseGateRecord]:
        stmt = select(ReleaseGateRecord).order_by(
            ReleaseGateRecord.created_at.desc()
        )
        if suite:
            stmt = stmt.where(ReleaseGateRecord.suite == suite)
        async with self._database.session_factory() as session:
            result = await session.scalars(stmt)
            return result.all()

    async def get_release_gate(
        self, gate_id: str
    ) -> ReleaseGateRecord:
        async with self._database.session_factory() as session:
            record = await session.get(ReleaseGateRecord, gate_id)
            if record is None:
                raise ResourceNotFoundError("release_gate", gate_id)
            return record

    async def create_gate_result(
        self,
        *,
        gate_id: str,
        evaluation_run_id: str,
        decision: str,
        reason: str,
        details: dict[str, object],
        exempted_by: str | None = None,
        exempt_reason: str | None = None,
        exempt_expires_at=None,
    ) -> EvaluationGateResultRecord:
        """幂等：同一 gate+run 只保留一条结果（重复判定更新）。"""
        async with self._database.session_factory() as session:
            existing = await session.scalar(
                select(EvaluationGateResultRecord).where(
                    EvaluationGateResultRecord.gate_id == gate_id,
                    EvaluationGateResultRecord.evaluation_run_id == evaluation_run_id,
                )
            )
            if existing is not None:
                existing.decision = decision
                existing.reason = reason
                existing.details = details
                existing.exempted_by = exempted_by
                existing.exempt_reason = exempt_reason
                existing.exempt_expires_at = exempt_expires_at
                record = existing
            else:
                record = EvaluationGateResultRecord(
                    gate_id=gate_id,
                    evaluation_run_id=evaluation_run_id,
                    decision=decision,
                    reason=reason,
                    details=details,
                    exempted_by=exempted_by,
                    exempt_reason=exempt_reason,
                    exempt_expires_at=exempt_expires_at,
                )
                session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_gate_results(
        self, evaluation_run_id: str
    ) -> Sequence[EvaluationGateResultRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(EvaluationGateResultRecord)
                .where(
                    EvaluationGateResultRecord.evaluation_run_id
                    == evaluation_run_id,
                )
            )
            return result.all()

    # ------------------------------------------------------------------
    # M15: 沙箱执行与网络出口审计
    # ------------------------------------------------------------------

    async def record_sandbox_execution(
        self,
        record: dict[str, object],
    ) -> None:
        """记录一次受限工具执行的隔离运行（不含秘密/参数明文）。"""
        from datetime import UTC as _UTC

        now = datetime.now(_UTC)
        started = record.get("started_at")
        finished = record.get("finished_at")
        row = SandboxExecutionRecord(
            tool_call_id=str(record.get("tool_call_id") or ""),
            run_id=record.get("run_id"),
            tool_name=str(record.get("tool_name") or ""),
            execution_class=str(record.get("execution_class") or "restricted_process"),
            status=str(record.get("status") or "pending"),
            resource_usage=dict(record.get("resource_usage") or {}),
            termination_reason=record.get("termination_reason"),
            policy_version=record.get("policy_version"),
            started_at=started if started is not None else now,
            finished_at=finished,
        )
        async with self._database.session_factory() as session:
            session.add(row)
            await session.commit()

    async def record_egress_event(
        self,
        record: dict[str, object],
    ) -> None:
        """记录一次出口决策（allow/deny 与字节计量）。"""
        row = EgressAuditEventRecord(
            tool_call_id=record.get("tool_call_id"),
            tool_name=str(record.get("tool_name") or "egress"),
            url=str(record.get("url") or ""),
            host=str(record.get("host") or ""),
            decision=str(record.get("decision") or "deny"),
            reason=str(record.get("reason") or ""),
            bytes_sent=int(record.get("bytes_sent") or 0),
            bytes_received=int(record.get("bytes_received") or 0),
            request_count=int(record.get("request_count") or 1),
        )
        async with self._database.session_factory() as session:
            session.add(row)
            await session.commit()
