"""Integrity risk persistence (07)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ResourceNotFoundError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    BehaviorFeatureSnapshotRecord,
    CoordinationClusterRecord,
    CoordinationMemberRecord,
    RiskAssessmentRecord,
    RiskPolicyVersionRecord,
)


def _now() -> datetime:
    return datetime.now(UTC)


class IntegrityRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert_risk_assessment(
        self,
        *,
        case_id: str,
        subject_type: str,
        subject_id: str,
        risk_type: str,
        score: float,
        band: str,
        reason_codes: list[str] | None = None,
        evidence_refs: dict[str, object] | None = None,
        model_version: str = "1.0.0",
    ) -> RiskAssessmentRecord:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(RiskAssessmentRecord).where(
                    RiskAssessmentRecord.case_id == case_id,
                    RiskAssessmentRecord.subject_type == subject_type,
                    RiskAssessmentRecord.subject_id == subject_id,
                    RiskAssessmentRecord.risk_type == risk_type,
                    RiskAssessmentRecord.model_version == model_version,
                )
            )
            if record is None:
                record = RiskAssessmentRecord(
                    case_id=case_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    risk_type=risk_type,
                    score=score,
                    band=band,
                    reason_codes=reason_codes or [],
                    evidence_refs=evidence_refs or {},
                    model_version=model_version,
                )
                session.add(record)
            else:
                record.score = score
                record.band = band
                record.reason_codes = reason_codes or record.reason_codes
                record.evidence_refs = evidence_refs or record.evidence_refs
                record.updated_at = _now()
            await session.commit()
            await session.refresh(record)
        return record

    async def list_assessments(
        self,
        case_id: str,
        *,
        risk_type: str | None = None,
        band: str | None = None,
        limit: int = 500,
    ) -> Sequence[RiskAssessmentRecord]:
        query = select(RiskAssessmentRecord).where(RiskAssessmentRecord.case_id == case_id)
        if risk_type is not None:
            query = query.where(RiskAssessmentRecord.risk_type == risk_type)
        if band is not None:
            query = query.where(RiskAssessmentRecord.band == band)
        query = query.order_by(RiskAssessmentRecord.score.desc()).limit(limit)
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def get_assessment(self, assessment_id: str) -> RiskAssessmentRecord:
        async with self._database.session_factory() as session:
            record = await session.get(RiskAssessmentRecord, assessment_id)
            if record is None:
                raise ResourceNotFoundError("risk assessment", assessment_id)
            return record

    async def review_assessment(
        self,
        assessment_id: str,
        status: str,
        *,
        by: str | None = None,
        note: str = "",
    ) -> RiskAssessmentRecord:
        async with self._database.session_factory() as session:
            record = await session.get(RiskAssessmentRecord, assessment_id)
            if record is None:
                raise ResourceNotFoundError("risk assessment", assessment_id)
            record.status = status
            if by is not None:
                record.reviewed_by = by
            record.review_note = note
            record.reviewed_at = _now()
            record.updated_at = _now()
            await session.commit()
            await session.refresh(record)
        return record

    async def create_cluster(
        self,
        *,
        case_id: str,
        size: int,
        score: float,
        explanation: str = "",
        algorithm_version: str = "1.0.0",
        fingerprint: str | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        members: list[dict[str, Any]] | None = None,
    ) -> CoordinationClusterRecord:
        async with self._database.session_factory() as session:
            if fingerprint:
                existing = await session.scalar(
                    select(CoordinationClusterRecord).where(
                        CoordinationClusterRecord.case_id == case_id,
                        CoordinationClusterRecord.fingerprint == fingerprint,
                    )
                )
                if existing is not None:
                    return existing
            record = CoordinationClusterRecord(
                case_id=case_id,
                size=size,
                score=score,
                explanation=explanation,
                algorithm_version=algorithm_version,
                fingerprint=fingerprint,
                window_start=window_start,
                window_end=window_end,
            )
            session.add(record)
            await session.flush()
            for member in members or []:
                session.add(
                    CoordinationMemberRecord(
                        cluster_id=record.id,
                        account_id=str(member["account_id"]),
                        membership_score=float(member.get("score", 0)),
                        role=str(member.get("role", "member")),
                        evidence=member.get("evidence", {}),
                    )
                )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if fingerprint:
                    existing = await session.scalar(
                        select(CoordinationClusterRecord).where(
                            CoordinationClusterRecord.case_id == case_id,
                            CoordinationClusterRecord.fingerprint == fingerprint,
                        )
                    )
                    if existing is not None:
                        return existing
                raise
            await session.refresh(record)
        return record

    async def list_clusters(self, case_id: str) -> Sequence[CoordinationClusterRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(CoordinationClusterRecord).where(
                        CoordinationClusterRecord.case_id == case_id
                    )
                )
            ).all()

    async def get_cluster(self, cluster_id: str) -> CoordinationClusterRecord:
        async with self._database.session_factory() as session:
            record = await session.get(CoordinationClusterRecord, cluster_id)
            if record is None:
                raise ResourceNotFoundError("coordination cluster", cluster_id)
            return record

    async def list_cluster_members(
        self,
        cluster_id: str,
    ) -> Sequence[CoordinationMemberRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(CoordinationMemberRecord).where(
                        CoordinationMemberRecord.cluster_id == cluster_id
                    )
                )
            ).all()

    async def create_behavior_snapshot(
        self,
        *,
        case_id: str,
        subject_type: str,
        subject_id: str,
        feature_name: str,
        feature_value: float,
        coverage: dict[str, object] | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        extract_version: str = "1.0.0",
    ) -> BehaviorFeatureSnapshotRecord:
        record = BehaviorFeatureSnapshotRecord(
            case_id=case_id,
            subject_type=subject_type,
            subject_id=subject_id,
            feature_name=feature_name,
            feature_value=feature_value,
            coverage=coverage or {},
            window_start=window_start,
            window_end=window_end,
            extract_version=extract_version,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(BehaviorFeatureSnapshotRecord).where(
                        BehaviorFeatureSnapshotRecord.case_id == case_id,
                        BehaviorFeatureSnapshotRecord.subject_type == subject_type,
                        BehaviorFeatureSnapshotRecord.subject_id == subject_id,
                        BehaviorFeatureSnapshotRecord.feature_name == feature_name,
                        BehaviorFeatureSnapshotRecord.window_start == window_start,
                        BehaviorFeatureSnapshotRecord.extract_version == extract_version,
                    )
                )
                assert existing is not None
                return existing
            await session.refresh(record)
        return record

    async def upsert_policy(
        self,
        *,
        version: str,
        thresholds: dict[str, object] | None = None,
        weights: dict[str, object] | None = None,
        platforms: list[str] | None = None,
    ) -> RiskPolicyVersionRecord:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(RiskPolicyVersionRecord).where(
                    RiskPolicyVersionRecord.version == version
                )
            )
            if record is None:
                record = RiskPolicyVersionRecord(
                    version=version,
                    thresholds=thresholds or {},
                    weights=weights or {},
                    platforms=platforms or [],
                )
                session.add(record)
            else:
                record.thresholds = thresholds or record.thresholds
                record.weights = weights or record.weights
                record.platforms = platforms or record.platforms
            await session.commit()
            await session.refresh(record)
        return record


    async def get_effective_policy(
        self,
        platform: str,
        *,
        at: datetime | None = None,
    ) -> RiskPolicyVersionRecord | None:
        """Return the newest effective global or platform-specific policy."""
        effective_at = at or _now()
        async with self._database.session_factory() as session:
            records = (
                await session.scalars(
                    select(RiskPolicyVersionRecord)
                    .where(RiskPolicyVersionRecord.effective_at <= effective_at)
                    .order_by(RiskPolicyVersionRecord.effective_at.desc())
                )
            ).all()
        for record in records:
            if not record.platforms or platform in record.platforms:
                return record
        return None
