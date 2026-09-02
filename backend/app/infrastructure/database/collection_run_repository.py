"""Durable background social collection run persistence (async progressive).

CollectionRun 是审批冻结 snapshot 的可恢复执行记录：租约（lease_owner /
lease_expires_at）+ 心跳（heartbeat_at）+ 平台级 checkpoint（progress_json）
保证 Worker crash / 重启后可从已完成平台续跑（INV-2 / INV-4）。

所有运行中写方法都必须带 lease fencing：``WHERE id = ... AND lease_owner =
:worker_id``，避免失去租约的 Worker 继续产生副作用（INV-2）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ResourceNotFoundError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import CollectionRunRecord

COLLECTION_RUN_STATUSES = (
    "queued",
    "running",
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
)
PLATFORM_STATUSES = ("queued", "running", "completed", "failed", "cancelled")

_ACTIVE_STATUSES = ("queued", "running")


def _now() -> datetime:
    return datetime.now(UTC)


class CollectionRunRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    # ---------------- create / read ----------------

    async def create(
        self,
        *,
        case_id: str,
        request_fingerprint: str,
        request_json: dict[str, Any],
        phase: str = "discovery",
        collection_definition_id: str | None = None,
        collection_definition_version: int | None = None,
        trigger_run_id: str | None = None,
        trigger_turn_id: str | None = None,
        trigger_tool_call_id: str | None = None,
        approval_id: str | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 10,
    ) -> CollectionRunRecord:
        """幂等创建 queued run；同 idempotency_key 已存在时返回已有记录。"""
        normalized_key = idempotency_key.strip()[:64] if idempotency_key else None
        record = CollectionRunRecord(
            case_id=case_id,
            phase=phase,
            status="queued",
            request_fingerprint=request_fingerprint,
            request_json=request_json,
            collection_definition_id=collection_definition_id,
            collection_definition_version=collection_definition_version,
            trigger_run_id=trigger_run_id,
            trigger_turn_id=trigger_turn_id,
            trigger_tool_call_id=trigger_tool_call_id,
            approval_id=approval_id,
            idempotency_key=normalized_key,
            max_attempts=max(1, max_attempts),
            progress_json={
                "platforms": {
                    platform: {
                        "status": "queued",
                        "attempts": 0,
                        "posts_collected": 0,
                        "comments_collected": 0,
                        "started_at": None,
                        "completed_at": None,
                        "error_code": None,
                        "error_message": None,
                    }
                    for platform in (request_json.get("platforms") or [])
                },
                "completed_platforms": 0,
                "total_platforms": len(request_json.get("platforms") or []),
            },
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
                    select(CollectionRunRecord).where(
                        CollectionRunRecord.case_id == case_id,
                        CollectionRunRecord.idempotency_key == normalized_key,
                    )
                )
                assert existing is not None
                return existing
            await session.refresh(record)
        return record

    async def get(self, run_id: str) -> CollectionRunRecord:
        async with self._database.session_factory() as session:
            record = await session.get(CollectionRunRecord, run_id)
            if record is None:
                raise ResourceNotFoundError("collection run", run_id)
            return record

    async def get_for_case(self, case_id: str, run_id: str) -> CollectionRunRecord:
        record = await self.get(run_id)
        if record.case_id != case_id:
            raise ResourceNotFoundError("collection run", run_id)
        return record

    async def list_for_case(
        self,
        case_id: str,
        *,
        active_only: bool = False,
        status: str | None = None,
        phase: str | None = None,
        limit: int = 20,
    ) -> Sequence[CollectionRunRecord]:
        query = select(CollectionRunRecord).where(
            CollectionRunRecord.case_id == case_id
        )
        if active_only:
            query = query.where(
                CollectionRunRecord.status.in_(_ACTIVE_STATUSES)
            )
        if status is not None:
            query = query.where(CollectionRunRecord.status == status)
        if phase is not None:
            query = query.where(CollectionRunRecord.phase == phase)
        query = query.order_by(CollectionRunRecord.created_at.desc()).limit(limit)
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def list_active_for_case(
        self, case_id: str, *, limit: int = 10
    ) -> Sequence[CollectionRunRecord]:
        return await self.list_for_case(case_id, active_only=True, limit=limit)

    async def count_for_case(self, case_id: str) -> int:
        async with self._database.session_factory() as session:
            value = await session.scalar(
                select(func.count(CollectionRunRecord.id)).where(
                    CollectionRunRecord.case_id == case_id
                )
            )
            return int(value or 0)

    async def list_active_by_trigger_run(
        self, trigger_run_id: str
    ) -> Sequence[CollectionRunRecord]:
        """级联取消用：某个 agent run 触发的所有 queued/running 采集。"""
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(CollectionRunRecord)
                .where(
                    CollectionRunRecord.trigger_run_id == trigger_run_id,
                    CollectionRunRecord.status.in_(_ACTIVE_STATUSES),
                )
                .order_by(CollectionRunRecord.created_at.asc())
            )
            return result.scalars().all()

    async def find_active_by_fingerprint(
        self, case_id: str, request_fingerprint: str
    ) -> CollectionRunRecord | None:
        """Active Equivalent：同 case + 同 fingerprint + queued/running。"""
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(CollectionRunRecord)
                .where(
                    CollectionRunRecord.case_id == case_id,
                    CollectionRunRecord.request_fingerprint == request_fingerprint,
                    CollectionRunRecord.status.in_(_ACTIVE_STATUSES),
                )
                .order_by(CollectionRunRecord.created_at.asc())
                .limit(1)
            )
            return result.scalars().first()

    # ---------------- claim / lease / heartbeat ----------------

    async def claim_next(
        self, worker_id: str, lease_seconds: int
    ) -> CollectionRunRecord | None:
        now = _now()
        async with self._database.session_factory() as session:
            # 租约过期且已耗尽 claim 次数：显式关闭，避免永远 running。
            await session.execute(
                update(CollectionRunRecord)
                .where(
                    CollectionRunRecord.status == "running",
                    CollectionRunRecord.lease_expires_at < now,
                    CollectionRunRecord.attempts >= CollectionRunRecord.max_attempts,
                )
                .values(
                    status="failed",
                    error_code="lease_expired_after_max_attempts",
                    error_message=(
                        "worker lease expired and the run exhausted its claims"
                    ),
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    completed_at=now,
                )
            )
            query = (
                select(CollectionRunRecord.id)
                .where(
                    CollectionRunRecord.cancel_requested_at.is_(None),
                    CollectionRunRecord.attempts < CollectionRunRecord.max_attempts,
                    or_(
                        CollectionRunRecord.status == "queued",
                        (
                            (CollectionRunRecord.status == "running")
                            & (CollectionRunRecord.lease_expires_at < now)
                        ),
                    ),
                )
                .order_by(CollectionRunRecord.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            run_id = await session.scalar(query)
            if run_id is None:
                await session.commit()
                return None
            record = await session.get(CollectionRunRecord, run_id)
            assert record is not None
            record.status = "running"
            record.attempts += 1
            record.lease_owner = worker_id
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.heartbeat_at = now
            if record.started_at is None:
                record.started_at = now
            await session.commit()
            await session.refresh(record)
            return record

    async def heartbeat(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> tuple[bool, bool]:
        """刷新租约并检查取消请求。返回 ``(owns_lease, cancel_requested)``。"""
        async with self._database.session_factory() as session:
            current = await session.get(CollectionRunRecord, run_id)
            if current is None or current.lease_owner != worker_id:
                return False, False
            cancel_requested = current.cancel_requested_at is not None
            if current.status != "running":
                return False, cancel_requested
            current.lease_expires_at = _now() + timedelta(seconds=lease_seconds)
            current.heartbeat_at = _now()
            await session.commit()
            return True, cancel_requested

    async def request_cancel(self, run_id: str) -> CollectionRunRecord:
        """用户取消：非终态 run 记录取消请求；queued 直接置 cancelled。"""
        record = await self.get(run_id)
        async with self._database.session_factory() as session:
            current = await session.get(CollectionRunRecord, run_id)
            assert current is not None
            if current.status == "queued":
                current.status = "cancelled"
                current.cancel_requested_at = _now()
                current.completed_at = _now()
            elif current.status in {"running", "completed_with_errors"}:
                current.cancel_requested_at = _now()
            await session.commit()
            await session.refresh(current)
            return current

    # ---------------- lease-fenced progress / result / terminal ----------------

    async def update_progress_if_owner(
        self,
        run_id: str,
        worker_id: str,
        *,
        progress_json: dict[str, Any],
        posts_collected: int,
        comments_collected: int,
    ) -> bool:
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(CollectionRunRecord)
                .where(
                    CollectionRunRecord.id == run_id,
                    CollectionRunRecord.lease_owner == worker_id,
                    CollectionRunRecord.status == "running",
                )
                .values(
                    progress_json=progress_json,
                    posts_collected=posts_collected,
                    comments_collected=comments_collected,
                    heartbeat_at=_now(),
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def update_result_if_owner(
        self, run_id: str, worker_id: str, result_json: dict[str, Any]
    ) -> bool:
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(CollectionRunRecord)
                .where(
                    CollectionRunRecord.id == run_id,
                    CollectionRunRecord.lease_owner == worker_id,
                    CollectionRunRecord.status == "running",
                )
                .values(result_json=result_json, heartbeat_at=_now())
            )
            await session.commit()
            return result.rowcount == 1

    async def mark_completed_if_owner(
        self, run_id: str, worker_id: str, result_json: dict[str, Any]
    ) -> bool:
        return await self._mark_terminal_if_owner(
            run_id, worker_id, "completed", result_json
        )

    async def mark_completed_with_errors_if_owner(
        self, run_id: str, worker_id: str, result_json: dict[str, Any]
    ) -> bool:
        return await self._mark_terminal_if_owner(
            run_id, worker_id, "completed_with_errors", result_json
        )

    async def mark_failed_if_owner(
        self, run_id: str, worker_id: str, result_json: dict[str, Any]
    ) -> bool:
        return await self._mark_terminal_if_owner(
            run_id, worker_id, "failed", result_json
        )

    async def mark_cancelled_if_owner(
        self, run_id: str, worker_id: str, result_json: dict[str, Any]
    ) -> bool:
        return await self._mark_terminal_if_owner(
            run_id, worker_id, "cancelled", result_json
        )

    async def _mark_terminal_if_owner(
        self,
        run_id: str,
        worker_id: str,
        status: str,
        result_json: dict[str, Any],
    ) -> bool:
        now = _now()
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(CollectionRunRecord)
                .where(
                    CollectionRunRecord.id == run_id,
                    CollectionRunRecord.lease_owner == worker_id,
                    CollectionRunRecord.status == "running",
                )
                .values(
                    status=status,
                    result_json=result_json,
                    error_code=result_json.get("error_code"),
                    error_message=result_json.get("error_message"),
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    completed_at=now,
                )
            )
            await session.commit()
            return result.rowcount == 1

    # ---------------- recovery ----------------

    async def recover_expired(self, worker_id: str, lease_seconds: int) -> int:
        """把过期租约、cancel_requested 的 running run 收敛为终态。

        claim_next 已能领走过期租约的 running；这里只处理带取消请求或
        已超过 claim 上限的残留，返回修正行数。
        """
        from sqlalchemy import case

        now = _now()
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(CollectionRunRecord)
                .where(
                    CollectionRunRecord.status == "running",
                    CollectionRunRecord.lease_expires_at < now,
                    or_(
                        CollectionRunRecord.cancel_requested_at.isnot(None),
                        CollectionRunRecord.attempts >= CollectionRunRecord.max_attempts,
                    ),
                )
                .values(
                    status=case(
                        (
                            CollectionRunRecord.cancel_requested_at.isnot(None),
                            "cancelled",
                        ),
                        else_="failed",
                    ),
                    error_code="lease_expired",
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    completed_at=now,
                )
            )
            await session.commit()
            return int(result.rowcount or 0)
