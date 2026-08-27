"""Media pipeline persistence (04 多模态流水线)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ResourceNotFoundError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    MediaAssetRecord,
    MediaDerivativeRecord,
    MediaPipelineJobRecord,
    MediaTranscriptRecord,
)


def _now() -> datetime:
    return datetime.now(UTC)


class MediaPipelineRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    # ---- assets ---------------------------------------------------------

    async def get_asset(self, asset_id: str) -> MediaAssetRecord:
        async with self._database.session_factory() as session:
            record = await session.get(MediaAssetRecord, asset_id)
            if record is None:
                raise ResourceNotFoundError("media asset", asset_id)
            return record

    async def list_assets_by_case(
        self,
        case_id: str,
        *,
        limit: int = 200,
    ) -> Sequence[MediaAssetRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(MediaAssetRecord)
                    .where(MediaAssetRecord.case_id == case_id)
                    .order_by(MediaAssetRecord.created_at.asc())
                    .limit(limit)
                )
            ).all()

    async def list_pending_assets(
        self,
        *,
        limit: int = 50,
    ) -> Sequence[MediaAssetRecord]:
        """资产尚未下载或尚未分析完成，且未处于失败终态。"""
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(MediaAssetRecord)
                    .where(
                        or_(
                            MediaAssetRecord.download_status == "not_downloaded",
                            MediaAssetRecord.download_status == "failed",
                            MediaAssetRecord.analysis_status == "pending",
                            MediaAssetRecord.analysis_status == "partial",
                        ),
                        MediaAssetRecord.analysis_status != "failed",
                    )
                    .order_by(MediaAssetRecord.created_at.asc())
                    .limit(limit)
                )
            ).all()

    async def update_asset(
        self,
        asset_id: str,
        **fields: Any,
    ) -> MediaAssetRecord:
        async with self._database.session_factory() as session:
            record = await session.get(MediaAssetRecord, asset_id)
            if record is None:
                raise ResourceNotFoundError("media asset", asset_id)
            for key, value in fields.items():
                setattr(record, key, value)
            await session.commit()
            await session.refresh(record)
        return record

    # ---- jobs -----------------------------------------------------------

    async def create_job(
        self,
        asset_id: str,
        stage: str,
        pipeline_version: str = "1.0.0",
    ) -> MediaPipelineJobRecord | None:
        record = MediaPipelineJobRecord(
            asset_id=asset_id,
            stage=stage,
            pipeline_version=pipeline_version,
            status="pending",
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

    async def get_job(self, job_id: str) -> MediaPipelineJobRecord:
        async with self._database.session_factory() as session:
            record = await session.get(MediaPipelineJobRecord, job_id)
            if record is None:
                raise ResourceNotFoundError("media pipeline job", job_id)
            return record

    async def list_jobs(self, asset_id: str) -> Sequence[MediaPipelineJobRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(MediaPipelineJobRecord)
                    .where(MediaPipelineJobRecord.asset_id == asset_id)
                    .order_by(MediaPipelineJobRecord.stage.asc())
                )
            ).all()

    async def claim_job(
        self,
        worker_id: str,
        lease_seconds: int,
        max_attempts: int = 3,
    ) -> MediaPipelineJobRecord | None:
        now = _now()
        async with self._database.session_factory() as session:
            job_id = await session.scalar(
                select(MediaPipelineJobRecord.id)
                .where(
                    MediaPipelineJobRecord.status.in_(["pending", "failed", "running"]),
                    MediaPipelineJobRecord.attempt < max_attempts,
                    or_(
                        MediaPipelineJobRecord.lease_expires_at.is_(None),
                        MediaPipelineJobRecord.lease_expires_at < now,
                    ),
                    or_(
                        MediaPipelineJobRecord.next_retry_at.is_(None),
                        MediaPipelineJobRecord.next_retry_at <= now,
                    ),
                )
                .order_by(MediaPipelineJobRecord.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job_id is None:
                await session.commit()
                return None
            record = await session.get(MediaPipelineJobRecord, job_id)
            assert record is not None
            record.status = "running"
            record.attempt += 1
            record.lease_owner = worker_id
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            await session.commit()
            await session.refresh(record)
            return record

    async def terminalize_expired_jobs(self, max_attempts: int = 3) -> list[str]:
        """Close final-attempt jobs whose worker lease expired."""
        now = _now()
        async with self._database.session_factory() as session:
            asset_ids = list(
                await session.scalars(
                    select(MediaPipelineJobRecord.asset_id).where(
                        MediaPipelineJobRecord.status == "running",
                        MediaPipelineJobRecord.lease_expires_at < now,
                        MediaPipelineJobRecord.attempt >= max_attempts,
                    )
                )
            )
            if asset_ids:
                await session.execute(
                    update(MediaPipelineJobRecord)
                    .where(
                        MediaPipelineJobRecord.status == "running",
                        MediaPipelineJobRecord.lease_expires_at < now,
                        MediaPipelineJobRecord.attempt >= max_attempts,
                    )
                    .values(
                        status="failed_terminal",
                        error_code="lease_expired_after_max_attempts",
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                )
            await session.commit()
            return list(dict.fromkeys(asset_ids))

    async def refresh_job_lease(
        self,
        job_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(MediaPipelineJobRecord)
                .where(
                    MediaPipelineJobRecord.id == job_id,
                    MediaPipelineJobRecord.status == "running",
                    MediaPipelineJobRecord.lease_owner == worker_id,
                )
                .values(lease_expires_at=_now() + timedelta(seconds=lease_seconds))
            )
            await session.commit()
            return result.rowcount == 1

    async def update_job(
        self,
        job_id: str,
        **fields: Any,
    ) -> MediaPipelineJobRecord:
        async with self._database.session_factory() as session:
            record = await session.get(MediaPipelineJobRecord, job_id)
            if record is None:
                raise ResourceNotFoundError("media pipeline job", job_id)
            for key, value in fields.items():
                setattr(record, key, value)
            await session.commit()
            await session.refresh(record)
        return record

    # ---- derivatives / transcripts --------------------------------------

    async def create_derivative(
        self,
        *,
        asset_id: str,
        kind: str,
        storage_uri: str | None = None,
        sha256: str | None = None,
        time_start_ms: int | None = None,
        time_end_ms: int | None = None,
        bbox: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
        producer: str = "",
        version: str = "1.0.0",
    ) -> MediaDerivativeRecord:
        record = MediaDerivativeRecord(
            asset_id=asset_id,
            kind=kind,
            storage_uri=storage_uri,
            sha256=sha256,
            time_start_ms=time_start_ms,
            time_end_ms=time_end_ms,
            bbox=bbox,
            metadata_json=metadata or {},
            producer=producer,
            version=version,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(MediaDerivativeRecord).where(
                        MediaDerivativeRecord.asset_id == asset_id,
                        MediaDerivativeRecord.kind == kind,
                        MediaDerivativeRecord.producer == producer,
                        MediaDerivativeRecord.version == version,
                    )
                )
                assert existing is not None
                return existing
            await session.refresh(record)
        return record

    async def create_transcript(
        self,
        *,
        asset_id: str,
        kind: str,
        language: str = "",
        segments: list[dict[str, object]] | None = None,
        full_text: str = "",
        confidence: float = 0,
        provider: str = "",
        version: str = "1.0.0",
    ) -> MediaTranscriptRecord:
        record = MediaTranscriptRecord(
            asset_id=asset_id,
            kind=kind,
            language=language,
            segments=segments or [],
            full_text=full_text,
            confidence=confidence,
            provider=provider,
            version=version,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(MediaTranscriptRecord).where(
                        MediaTranscriptRecord.asset_id == asset_id,
                        MediaTranscriptRecord.kind == kind,
                        MediaTranscriptRecord.provider == provider,
                        MediaTranscriptRecord.version == version,
                    )
                )
                assert existing is not None
                return existing
            await session.refresh(record)
        return record

    async def list_transcripts(self, asset_id: str) -> Sequence[MediaTranscriptRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(MediaTranscriptRecord).where(MediaTranscriptRecord.asset_id == asset_id)
                )
            ).all()
