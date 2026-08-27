"""Uncertainty & bias persistence (08)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ResourceNotFoundError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    AlternativeHypothesisRecord,
    AnalysisAssumptionRecord,
    ConclusionConfidenceRecord,
    QualityAssessmentRecord,
    SensitivityRunRecord,
)


def _now() -> datetime:
    return datetime.now(UTC)


class UncertaintyRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert_quality_assessment(
        self,
        *,
        case_id: str,
        target_type: str,
        target_id: str,
        dimension: str,
        level: str,
        score: float | None = None,
        method: str = "",
        inputs: dict[str, object] | None = None,
        limitations: list[str] | None = None,
        version: str = "1.0.0",
    ) -> QualityAssessmentRecord:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(QualityAssessmentRecord).where(
                    QualityAssessmentRecord.case_id == case_id,
                    QualityAssessmentRecord.target_type == target_type,
                    QualityAssessmentRecord.target_id == target_id,
                    QualityAssessmentRecord.dimension == dimension,
                    QualityAssessmentRecord.version == version,
                )
            )
            if record is None:
                record = QualityAssessmentRecord(
                    case_id=case_id,
                    target_type=target_type,
                    target_id=target_id,
                    dimension=dimension,
                    level=level,
                    score=score,
                    method=method,
                    inputs=inputs or {},
                    limitations=limitations or [],
                    version=version,
                )
                session.add(record)
            else:
                record.level = level
                record.score = score
                record.limitations = limitations or record.limitations
            await session.commit()
            await session.refresh(record)
        return record

    async def list_quality_assessments(
        self,
        case_id: str,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> Sequence[QualityAssessmentRecord]:
        query = select(QualityAssessmentRecord).where(QualityAssessmentRecord.case_id == case_id)
        if target_type is not None:
            query = query.where(QualityAssessmentRecord.target_type == target_type)
        if target_id is not None:
            query = query.where(QualityAssessmentRecord.target_id == target_id)
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def upsert_assumption(
        self,
        *,
        case_id: str,
        analysis_target: str,
        assumption_name: str,
        value: dict[str, object] | None = None,
        source: str = "system",
        editable: bool = True,
    ) -> AnalysisAssumptionRecord:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(AnalysisAssumptionRecord).where(
                    AnalysisAssumptionRecord.case_id == case_id,
                    AnalysisAssumptionRecord.analysis_target == analysis_target,
                    AnalysisAssumptionRecord.assumption_name == assumption_name,
                )
            )
            if record is None:
                record = AnalysisAssumptionRecord(
                    case_id=case_id,
                    analysis_target=analysis_target,
                    assumption_name=assumption_name,
                    value=value or {},
                    source=source,
                    editable=editable,
                )
                session.add(record)
            else:
                record.value = value or record.value
                record.updated_at = _now()
            await session.commit()
            await session.refresh(record)
        return record

    async def list_assumptions(self, case_id: str) -> Sequence[AnalysisAssumptionRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(AnalysisAssumptionRecord).where(
                        AnalysisAssumptionRecord.case_id == case_id
                    )
                )
            ).all()

    async def create_sensitivity_run(
        self,
        *,
        case_id: str,
        baseline_hash: str,
        baseline_params: dict[str, object] | None = None,
        variant_params: dict[str, object] | None = None,
        output_diff: dict[str, object] | None = None,
        status: str = "completed",
        cost: float = 0,
    ) -> SensitivityRunRecord | None:
        record = SensitivityRunRecord(
            case_id=case_id,
            baseline_hash=baseline_hash,
            baseline_params=baseline_params or {},
            variant_params=variant_params or {},
            output_diff=output_diff or {},
            status=status,
            cost=cost,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            await session.refresh(record)
        return record

    async def get_sensitivity_run(self, run_id: str) -> SensitivityRunRecord:
        async with self._database.session_factory() as session:
            record = await session.get(SensitivityRunRecord, run_id)
            if record is None:
                raise ResourceNotFoundError("sensitivity run", run_id)
            return record

    async def list_sensitivity_runs(self, case_id: str) -> Sequence[SensitivityRunRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(SensitivityRunRecord).where(SensitivityRunRecord.case_id == case_id)
                )
            ).all()

    async def create_hypothesis(
        self,
        *,
        case_id: str,
        statement: str,
        prediction: str = "",
        supporting_evidence: list[str] | None = None,
        opposing_evidence: list[str] | None = None,
        proposer: str = "system",
    ) -> AlternativeHypothesisRecord:
        record = AlternativeHypothesisRecord(
            case_id=case_id,
            statement=statement,
            prediction=prediction,
            supporting_evidence=supporting_evidence or [],
            opposing_evidence=opposing_evidence or [],
            proposer=proposer,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_hypotheses(self, case_id: str) -> Sequence[AlternativeHypothesisRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(AlternativeHypothesisRecord).where(
                        AlternativeHypothesisRecord.case_id == case_id
                    )
                )
            ).all()

    async def upsert_conclusion_confidence(
        self,
        *,
        case_id: str,
        conclusion_id: str,
        conclusion_text: str = "",
        dimensions: dict[str, object] | None = None,
        final_level: str = "low",
        forbidden_reasons: list[str] | None = None,
        calibration_version: str = "uncalibrated",
    ) -> ConclusionConfidenceRecord:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(ConclusionConfidenceRecord).where(
                    ConclusionConfidenceRecord.case_id == case_id,
                    ConclusionConfidenceRecord.conclusion_id == conclusion_id,
                    ConclusionConfidenceRecord.calibration_version == calibration_version,
                )
            )
            if record is None:
                record = ConclusionConfidenceRecord(
                    case_id=case_id,
                    conclusion_id=conclusion_id,
                    conclusion_text=conclusion_text,
                    dimensions=dimensions or {},
                    final_level=final_level,
                    forbidden_reasons=forbidden_reasons or [],
                    calibration_version=calibration_version,
                )
                session.add(record)
            else:
                record.dimensions = dimensions or record.dimensions
                record.final_level = final_level
                record.forbidden_reasons = forbidden_reasons or record.forbidden_reasons
            await session.commit()
            await session.refresh(record)
        return record

    async def list_conclusions(self, case_id: str) -> Sequence[ConclusionConfidenceRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(ConclusionConfidenceRecord).where(
                        ConclusionConfidenceRecord.case_id == case_id
                    )
                )
            ).all()
