"""Durable asynchronous analysis-job persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ResourceNotFoundError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import AnalysisJobRecord


def _now() -> datetime:
    return datetime.now(UTC)


class AnalysisJobRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_job(
        self,
        *,
        case_id: str,
        job_type: str,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> AnalysisJobRecord:
        normalized_key = idempotency_key.strip()[:64] if idempotency_key else None
        record = AnalysisJobRecord(
            case_id=case_id,
            job_type=job_type,
            status="pending",
            idempotency_key=normalized_key,
            max_attempts=max(1, max_attempts),
        )
        async with self._database.session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if normalized_key is None:
                    raise
                existing = await session.scalar(
                    select(AnalysisJobRecord).where(
                        AnalysisJobRecord.case_id == case_id,
                        AnalysisJobRecord.job_type == job_type,
                        AnalysisJobRecord.idempotency_key == normalized_key,
                    )
                )
                assert existing is not None
                return existing
            await session.refresh(record)
        return record

    async def get_job(self, job_id: str) -> AnalysisJobRecord:
        async with self._database.session_factory() as session:
            record = await session.get(AnalysisJobRecord, job_id)
            if record is None:
                raise ResourceNotFoundError("analysis job", job_id)
            return record

    async def latest_succeeded(
        self, case_id: str, job_type: str
    ) -> AnalysisJobRecord | None:
        """V3 §52：最新 succeeded AnalysisJob（coordination detector scope）。"""
        query = (
            select(AnalysisJobRecord)
            .where(
                AnalysisJobRecord.case_id == case_id,
                AnalysisJobRecord.job_type == job_type,
                AnalysisJobRecord.status == "succeeded",
            )
            .order_by(AnalysisJobRecord.created_at.desc())
            .limit(1)
        )
        async with self._database.session_factory() as session:
            return await session.scalar(query)

    async def list_jobs(
        self,
        case_id: str,
        *,
        job_type: str | None = None,
        limit: int = 50,
    ) -> Sequence[AnalysisJobRecord]:
        query = select(AnalysisJobRecord).where(AnalysisJobRecord.case_id == case_id)
        if job_type is not None:
            query = query.where(AnalysisJobRecord.job_type == job_type)
        query = query.order_by(AnalysisJobRecord.created_at.desc()).limit(limit)
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def claim_job(
        self,
        worker_id: str,
        lease_seconds: int,
        job_type: str | None = None,
    ) -> AnalysisJobRecord | None:
        now = _now()
        async with self._database.session_factory() as session:
            # A worker may die during its final allowed attempt.  Such an
            # expired lease cannot be retried without exceeding max_attempts,
            # so close it explicitly instead of leaving it running forever.
            await session.execute(
                update(AnalysisJobRecord)
                .where(
                    AnalysisJobRecord.status == "running",
                    AnalysisJobRecord.lease_expires_at < now,
                    AnalysisJobRecord.attempt >= AnalysisJobRecord.max_attempts,
                )
                .values(
                    status="failed_terminal",
                    error_code="lease_expired_after_max_attempts",
                    progress_json={"phase": "failed_terminal"},
                    lease_owner=None,
                    lease_expires_at=None,
                )
            )
            query = (
                select(AnalysisJobRecord.id)
                .where(
                    AnalysisJobRecord.cancel_requested.is_(False),
                    AnalysisJobRecord.attempt < AnalysisJobRecord.max_attempts,
                    or_(
                        AnalysisJobRecord.status == "pending",
                        (
                            (AnalysisJobRecord.status == "retry_wait")
                            & or_(
                                AnalysisJobRecord.next_retry_at.is_(None),
                                AnalysisJobRecord.next_retry_at <= now,
                            )
                        ),
                        (
                            (AnalysisJobRecord.status == "running")
                            & (AnalysisJobRecord.lease_expires_at < now)
                        ),
                    ),
                )
                .order_by(AnalysisJobRecord.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job_type is not None:
                query = query.where(AnalysisJobRecord.job_type == job_type)
            job_id = await session.scalar(query)
            if job_id is None:
                await session.commit()
                return None
            record = await session.get(AnalysisJobRecord, job_id)
            assert record is not None
            record.status = "running"
            record.attempt += 1
            record.next_retry_at = None
            record.lease_owner = worker_id
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.progress_json = {"phase": "running", "attempt": record.attempt}
            await session.commit()
            await session.refresh(record)
            return record

    async def refresh_lease(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(AnalysisJobRecord)
                .where(
                    AnalysisJobRecord.id == job_id,
                    AnalysisJobRecord.status == "running",
                    AnalysisJobRecord.lease_owner == worker_id,
                    AnalysisJobRecord.cancel_requested.is_(False),
                )
                .values(lease_expires_at=_now() + timedelta(seconds=lease_seconds))
            )
            await session.commit()
            return result.rowcount == 1

    async def complete_job(self, job_id: str, worker_id: str, result_json: dict[str, Any]) -> bool:
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(AnalysisJobRecord)
                .where(
                    AnalysisJobRecord.id == job_id,
                    AnalysisJobRecord.status == "running",
                    AnalysisJobRecord.lease_owner == worker_id,
                    AnalysisJobRecord.cancel_requested.is_(False),
                )
                .values(
                    status="succeeded",
                    result_json=result_json,
                    progress_json={"phase": "completed"},
                    error_code=None,
                    lease_owner=None,
                    lease_expires_at=None,
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def fail_job(self, job_id: str, worker_id: str, error_code: str) -> bool:
        job = await self.get_job(job_id)
        terminal = job.attempt >= job.max_attempts
        values: dict[str, Any] = {
            "status": "failed_terminal" if terminal else "retry_wait",
            "error_code": error_code,
            "progress_json": {"phase": "failed", "attempt": job.attempt},
            "lease_owner": None,
            "lease_expires_at": None,
        }
        if not terminal:
            values["next_retry_at"] = _now() + timedelta(seconds=2**job.attempt)
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(AnalysisJobRecord)
                .where(
                    AnalysisJobRecord.id == job_id,
                    AnalysisJobRecord.status == "running",
                    AnalysisJobRecord.lease_owner == worker_id,
                )
                .values(**values)
            )
            await session.commit()
            return result.rowcount == 1

    async def request_cancel(self, job_id: str) -> AnalysisJobRecord:
        await self.get_job(job_id)
        async with self._database.session_factory() as session:
            record = await session.get(AnalysisJobRecord, job_id)
            assert record is not None
            if record.status in {"pending", "retry_wait"}:
                record.status = "cancelled"
                record.cancel_requested = True
                record.progress_json = {"phase": "cancelled"}
            elif record.status == "running":
                record.cancel_requested = True
            await session.commit()
            await session.refresh(record)
            return record

    async def mark_cancelled(self, job_id: str, worker_id: str) -> bool:
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(AnalysisJobRecord)
                .where(
                    AnalysisJobRecord.id == job_id,
                    AnalysisJobRecord.status == "running",
                    AnalysisJobRecord.lease_owner == worker_id,
                    AnalysisJobRecord.cancel_requested.is_(True),
                )
                .values(
                    status="cancelled",
                    progress_json={"phase": "cancelled"},
                    lease_owner=None,
                    lease_expires_at=None,
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def update_job(self, job_id: str, **fields: Any) -> AnalysisJobRecord:
        async with self._database.session_factory() as session:
            record = await session.get(AnalysisJobRecord, job_id)
            if record is None:
                raise ResourceNotFoundError("analysis job", job_id)
            for key, value in fields.items():
                setattr(record, key, value)
            await session.commit()
            await session.refresh(record)
            return record
