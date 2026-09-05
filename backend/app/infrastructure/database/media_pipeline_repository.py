"""Media pipeline persistence (04 多模态流水线)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
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

    # ---- V3 §38: cross-case media batch matching ------------------------

    async def list_case_media_hashes(
        self, case_id: str, *, limit: int = 20000
    ) -> list[tuple[str, str, str]]:
        """anchor case 的 (asset_id, actual_sha256, media_type)。"""
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(
                    MediaAssetRecord.id,
                    MediaAssetRecord.actual_sha256,
                    MediaAssetRecord.media_type,
                )
                .where(
                    MediaAssetRecord.case_id == case_id,
                    MediaAssetRecord.actual_sha256.isnot(None),
                    MediaAssetRecord.actual_sha256 != "",
                )
                .limit(limit)
            )
            return [
                (str(asset_id), str(sha256), str(media_type))
                for asset_id, sha256, media_type in rows.all()
            ]

    async def list_case_media_hashes_page(
        self,
        case_id: str,
        *,
        after_id: str | None = None,
        limit: int = 1000,
    ) -> list[tuple[str, str, str]]:
        """FC1：shared_media detector 专用 keyset 分页（asset id ASC）。

        返回 (asset_id, actual_sha256, media_type)；cursor = MediaAssetRecord.id。
        """
        query = (
            select(
                MediaAssetRecord.id,
                MediaAssetRecord.actual_sha256,
                MediaAssetRecord.media_type,
            )
            .where(
                MediaAssetRecord.case_id == case_id,
                MediaAssetRecord.actual_sha256.isnot(None),
                MediaAssetRecord.actual_sha256 != "",
            )
        )
        if after_id:
            query = query.where(MediaAssetRecord.id > after_id)
        query = query.order_by(MediaAssetRecord.id.asc()).limit(
            max(1, min(limit, 1000))
        )
        async with self._database.session_factory() as session:
            rows = await session.execute(query)
            return [
                (str(asset_id), str(sha256), str(media_type))
                for asset_id, sha256, media_type in rows.all()
            ]

    async def list_sha_case_counts_page(
        self,
        *,
        after_sha: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """FC1：media_reuse detector 专用 keyset 分页（actual_sha256 ASC）。

        只选择 COUNT(DISTINCT case_id) >= 2 的 SHA；每页先取 sha 列表，
        再用一次 IN 查询取 (sha, case_id) 分布并在 Python 聚合（禁止每个
        SHA 一个 SQL）。cursor = actual_sha256。
        """
        agg_query = (
            select(MediaAssetRecord.actual_sha256)
            .where(
                MediaAssetRecord.actual_sha256.isnot(None),
                MediaAssetRecord.actual_sha256 != "",
            )
        )
        if after_sha:
            agg_query = agg_query.where(MediaAssetRecord.actual_sha256 > after_sha)
        agg_query = (
            agg_query.group_by(MediaAssetRecord.actual_sha256)
            .having(func.count(func.distinct(MediaAssetRecord.case_id)) >= 2)
            .order_by(MediaAssetRecord.actual_sha256.asc())
            .limit(max(1, min(limit, 1000)))
        )
        async with self._database.session_factory() as session:
            shas = [str(row) for row in await session.scalars(agg_query)]
            if not shas:
                return []
            case_rows = await session.execute(
                select(
                    MediaAssetRecord.actual_sha256,
                    MediaAssetRecord.case_id,
                ).where(MediaAssetRecord.actual_sha256.in_(tuple(shas)))
            )
            cases_by_sha: dict[str, set[str]] = {sha: set() for sha in shas}
            for sha, case_id in case_rows.all():
                cases_by_sha[str(sha)].add(str(case_id))
        return [
            {
                "sha256": sha,
                "case_count": len(cases_by_sha[sha]),
                "case_ids": sorted(cases_by_sha[sha]),
            }
            for sha in shas
        ]

    async def list_sha_case_counts(self, *, limit: int = 5000) -> list[dict[str, Any]]:
        """V3 §54：全局 exact SHA → 出现过的 Case 列表（media_reuse 输入）。

        只统计出现在 >=2 个 Case 的 SHA（单 Case 的 SHA 不进高级告警流）。
        两次查询（先聚合哈希，再取 case 分布），跨方言安全。
        """
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(
                    MediaAssetRecord.actual_sha256,
                    func.count(func.distinct(MediaAssetRecord.case_id)).label(
                        "case_count"
                    ),
                )
                .where(
                    MediaAssetRecord.actual_sha256.isnot(None),
                    MediaAssetRecord.actual_sha256 != "",
                )
                .group_by(MediaAssetRecord.actual_sha256)
                .having(func.count(func.distinct(MediaAssetRecord.case_id)) >= 2)
                .order_by(func.count(func.distinct(MediaAssetRecord.case_id)).desc())
                .limit(limit)
            )
            candidates = [(str(sha), int(count)) for sha, count in rows.all()]
            if not candidates:
                return []
            sha_values = [sha for sha, _ in candidates]
            case_rows = await session.execute(
                select(
                    MediaAssetRecord.actual_sha256,
                    MediaAssetRecord.case_id,
                ).where(MediaAssetRecord.actual_sha256.in_(tuple(sha_values)))
            )
            cases_by_sha: dict[str, set[str]] = {sha: set() for sha, _ in candidates}
            for sha, case_id in case_rows.all():
                cases_by_sha[str(sha)].add(str(case_id))
        return [
            {
                "sha256": sha,
                "case_count": int(count),
                "case_ids": sorted(cases_by_sha.get(sha, ())),
            }
            for sha, count in candidates
        ]

    async def find_cross_case_sha_matches(
        self,
        case_id: str,
        sha256_values: Sequence[str],
        limit: int = 2000,
    ) -> Sequence[MediaAssetRecord]:
        """§38 Exact：不同 case 相同 actual_sha256（一次 IN 查询）。"""
        unique = sorted({value for value in sha256_values if value})
        if not unique:
            return []
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(MediaAssetRecord)
                    .where(
                        MediaAssetRecord.case_id != case_id,
                        MediaAssetRecord.actual_sha256.in_(tuple(unique)),
                    )
                    .order_by(MediaAssetRecord.case_id, MediaAssetRecord.id)
                    .limit(limit)
                )
            ).all()

    async def find_cross_case_phash_candidates(
        self,
        case_id: str,
        block_keys: Sequence[str],
        limit: int = 2000,
    ) -> Sequence[MediaAssetRecord]:
        """§38 Candidate：四段 phash blocking 的候选资产。

        单次 bounded 查询取其它 case 中 phash 非空的资产，调用方在内存侧
        复算 block key（f"{media_type}:{offset}:{phash[offset:offset+4]}"），
        避免每 asset 全表扫描（双方言安全，不做 SQL substring）。
        """
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(MediaAssetRecord)
                    .where(
                        MediaAssetRecord.case_id != case_id,
                        MediaAssetRecord.phash.isnot(None),
                        MediaAssetRecord.phash != "",
                    )
                    .order_by(MediaAssetRecord.case_id, MediaAssetRecord.id)
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
