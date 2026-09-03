"""V3 §12.1: Investigation Quality persistence (one latest record per case).

只保存当前最新 Quality（V3 §6 不做历史 snapshot）；upsert 以
UNIQUE(case_id) 为幂等锚点，刷新即覆盖。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    CaseRecord,
    InvestigationQualityRecord,
)

_ATTENTION_GRADES = ("needs_attention", "weak")


class InvestigationQualityRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, case_id: str) -> InvestigationQualityRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(InvestigationQualityRecord).where(
                    InvestigationQualityRecord.case_id == case_id
                )
            )

    async def upsert(
        self,
        *,
        case_id: str,
        overall_score: float | None,
        grade: str,
        dimensions: dict[str, Any],
        metrics: dict[str, Any],
        gaps: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        input_fingerprint: str,
        algorithm_version: str,
        computed_at: datetime,
    ) -> InvestigationQualityRecord:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(InvestigationQualityRecord).where(
                    InvestigationQualityRecord.case_id == case_id
                )
            )
            if record is None:
                record = InvestigationQualityRecord(
                    case_id=case_id,
                    overall_score=overall_score,
                    grade=grade,
                    dimensions_json=dimensions,
                    metrics_json=metrics,
                    gaps_json=gaps,
                    warnings_json=warnings,
                    input_fingerprint=input_fingerprint,
                    algorithm_version=algorithm_version,
                    computed_at=computed_at,
                )
                session.add(record)
                try:
                    await session.commit()
                    await session.refresh(record)
                    return record
                except IntegrityError:
                    # 并发 upsert：UNIQUE(case_id) 冲突时转为更新既有行
                    await session.rollback()
                    record = await session.scalar(
                        select(InvestigationQualityRecord).where(
                            InvestigationQualityRecord.case_id == case_id
                        )
                    )
                    if record is None:
                        raise
            record.overall_score = overall_score
            record.grade = grade
            # JSON 列必须整体替换新对象：原地 mutate 不触发 SQLAlchemy dirty
            record.dimensions_json = dict(dimensions)
            record.metrics_json = dict(metrics)
            record.gaps_json = list(gaps)
            record.warnings_json = list(warnings)
            record.input_fingerprint = input_fingerprint
            record.algorithm_version = algorithm_version
            record.computed_at = computed_at
            await session.commit()
            await session.refresh(record)
            return record

    async def list_needing_attention(
        self, limit: int = 5
    ) -> list[InvestigationQualityRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(InvestigationQualityRecord)
                .where(InvestigationQualityRecord.grade.in_(_ATTENTION_GRADES))
                .order_by(
                    InvestigationQualityRecord.overall_score.asc().nullsfirst(),
                    InvestigationQualityRecord.updated_at.desc(),
                )
                .limit(limit)
            )
            return list(result.all())

    async def count_by_grade(self) -> dict[str, int]:
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(
                    InvestigationQualityRecord.grade,
                    func.count(InvestigationQualityRecord.id),
                ).group_by(InvestigationQualityRecord.grade)
            )
            return {grade: int(count) for grade, count in rows.all()}

    async def count_unassessed(self, total_cases: int) -> int:
        async with self._database.session_factory() as session:
            assessed = await session.scalar(
                select(func.count(InvestigationQualityRecord.id))
            )
            return max(0, int(total_cases) - int(assessed or 0))

    async def count_cases(self) -> int:
        async with self._database.session_factory() as session:
            value = await session.scalar(select(func.count(CaseRecord.id)))
            return int(value or 0)
