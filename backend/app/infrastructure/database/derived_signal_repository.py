"""V3 §50: Derived Signal persistence（非 Monitor Alert 来源的高级信号）。

fingerprint 表示持续可追踪的 detector subject（§11.3）；detector_active
与 status 分离（§11.2）：detector_active 反映当前 detector 条件是否成立，
status 是用户/工作流状态。Case 过滤必须 JOIN derived_signal_case_links，
不得依赖 related_case_ids_json contains 做跨方言 JSON 查询。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    DerivedSignalCaseLinkRecord,
    DerivedSignalRecord,
)

# §11.2 true→false：open/acknowledged 自动 resolved；suppressed/resolved 保持
_AUTO_RESOLVE_STATUSES = ("open", "acknowledged")


def _now() -> datetime:
    return datetime.now(UTC)


class DerivedSignalRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert_observed_signal(
        self,
        *,
        fingerprint: str,
        case_id: str,
        source_type: str,
        source_id: str,
        signal_type: str,
        severity: str,
        title: str,
        why_it_matters: str,
        confidence: float | None,
        metric_snapshot: dict[str, Any],
        evidence_refs: list[object],
        related_case_ids: list[str],
        detector_version: str,
        case_links: Sequence[str] | None = None,
    ) -> DerivedSignalRecord:
        """§11.2 生命周期 upsert：新建 / true→true / false→true。

        case_links 为 None 时按 related_case_ids 写 case link 表。
        """
        now = _now()
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(DerivedSignalRecord).where(
                    DerivedSignalRecord.fingerprint == fingerprint
                )
            )
            if record is None:
                record = DerivedSignalRecord(
                    case_id=case_id,
                    source_type=source_type,
                    source_id=source_id,
                    signal_type=signal_type,
                    severity=severity,
                    status="open",
                    detector_active=True,
                    title=title,
                    why_it_matters=why_it_matters,
                    confidence=confidence,
                    metric_snapshot_json=metric_snapshot,
                    evidence_refs_json=evidence_refs,
                    related_case_ids_json=related_case_ids,
                    fingerprint=fingerprint,
                    detector_version=detector_version,
                    occurrence_count=1,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                session.add(record)
                await session.commit()
            else:
                # 条件重新出现（false→true）：非 suppressed → open + occurrence+1
                # （§11.2：resolved 也重新 open；suppressed 保持 suppressed 只计数）
                if not record.detector_active:
                    record.occurrence_count = (record.occurrence_count or 1) + 1
                    if record.status != "suppressed":
                        record.status = "open"
                        record.status_updated_at = now
                record.detector_active = True
                record.severity = severity
                record.title = title
                record.why_it_matters = why_it_matters
                record.confidence = confidence
                record.metric_snapshot_json = metric_snapshot
                record.evidence_refs_json = evidence_refs
                record.related_case_ids_json = related_case_ids
                record.last_seen_at = now
                await session.commit()

        await self.replace_case_links(
            record.id, list(case_links) if case_links is not None else related_case_ids
        )
        return record

    async def reconcile_detector_scope(
        self,
        *,
        signal_type: str,
        detector_version: str,
        case_ids: Sequence[str],
        expected_fingerprints: Sequence[str],
    ) -> int:
        """§56：detector scope 内不再成立的 Signal → detector_active=false。

        只处理本次 scope（signal_type + detector_version + case link 命中），
        不得影响其他 detector / 其他 Case 的 Signal。返回置 inactive 的数量。
        """
        expected = set(expected_fingerprints)
        query = (
            select(DerivedSignalRecord)
            .join(
                DerivedSignalCaseLinkRecord,
                DerivedSignalCaseLinkRecord.signal_id == DerivedSignalRecord.id,
            )
            .where(
                DerivedSignalRecord.signal_type == signal_type,
                DerivedSignalRecord.detector_version == detector_version,
                DerivedSignalRecord.detector_active.is_(True),
                DerivedSignalCaseLinkRecord.case_id.in_(list(case_ids)),
            )
            .distinct()
        )
        async with self._database.session_factory() as session:
            records = (await session.scalars(query)).all()
            now = _now()
            deactivated = 0
            for record in records:
                if record.fingerprint in expected:
                    continue
                record.detector_active = False
                if record.status in _AUTO_RESOLVE_STATUSES:
                    record.status = "resolved"
                    record.status_updated_at = now
                deactivated += 1
            if deactivated:
                await session.commit()
            return deactivated

    async def reconcile_detector_global(
        self,
        *,
        signal_type: str,
        detector_version: str,
        expected_fingerprints: Sequence[str],
    ) -> int:
        """V3 Rework R4：Workspace-global detector 全量对账。

        范围 = signal_type + detector_version + detector_active=true（不按
        Case 过滤，避免「主体完全消失后旧 Signal 不再落入 scope」的盲区）；
        不在 expected set 的 Signal → detector_active=false，生命周期继续
        （open/acknowledged → resolved；suppressed 保持）。
        """
        expected = set(expected_fingerprints)
        query = select(DerivedSignalRecord).where(
            DerivedSignalRecord.signal_type == signal_type,
            DerivedSignalRecord.detector_version == detector_version,
            DerivedSignalRecord.detector_active.is_(True),
        )
        async with self._database.session_factory() as session:
            records = (await session.scalars(query)).all()
            now = _now()
            deactivated = 0
            for record in records:
                if record.fingerprint in expected:
                    continue
                record.detector_active = False
                if record.status in _AUTO_RESOLVE_STATUSES:
                    record.status = "resolved"
                    record.status_updated_at = now
                deactivated += 1
            if deactivated:
                await session.commit()
            return deactivated

    async def get(self, signal_id: str) -> DerivedSignalRecord | None:
        async with self._database.session_factory() as session:
            return await session.get(DerivedSignalRecord, signal_id)

    async def list(
        self,
        *,
        statuses: Sequence[str] | None = None,
        severity: str | None = None,
        signal_type: str | None = None,
        source_type: str | None = None,
        detector_active: bool | None = None,
        limit: int = 100,
    ) -> Sequence[DerivedSignalRecord]:
        query = select(DerivedSignalRecord)
        if statuses:
            query = query.where(DerivedSignalRecord.status.in_(list(statuses)))
        if severity:
            query = query.where(DerivedSignalRecord.severity == severity)
        if signal_type:
            query = query.where(DerivedSignalRecord.signal_type == signal_type)
        if source_type:
            query = query.where(DerivedSignalRecord.source_type == source_type)
        if detector_active is not None:
            query = query.where(
                DerivedSignalRecord.detector_active.is_(detector_active)
            )
        query = query.order_by(DerivedSignalRecord.last_seen_at.desc()).limit(
            max(0, limit)
        )
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def list_for_case(
        self,
        case_id: str,
        *,
        statuses: Sequence[str] | None = None,
        severity: str | None = None,
        signal_type: str | None = None,
        detector_active: bool | None = None,
        limit: int = 100,
    ) -> Sequence[DerivedSignalRecord]:
        """§50：Case 过滤必须 JOIN derived_signal_case_links。"""
        query = (
            select(DerivedSignalRecord)
            .join(
                DerivedSignalCaseLinkRecord,
                DerivedSignalCaseLinkRecord.signal_id == DerivedSignalRecord.id,
            )
            .where(DerivedSignalCaseLinkRecord.case_id == case_id)
            .distinct()
        )
        if statuses:
            query = query.where(DerivedSignalRecord.status.in_(list(statuses)))
        if severity:
            query = query.where(DerivedSignalRecord.severity == severity)
        if signal_type:
            query = query.where(DerivedSignalRecord.signal_type == signal_type)
        if detector_active is not None:
            query = query.where(
                DerivedSignalRecord.detector_active.is_(detector_active)
            )
        query = query.order_by(DerivedSignalRecord.last_seen_at.desc()).limit(
            max(0, limit)
        )
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def set_status(
        self, signal_id: str, status: str
    ) -> DerivedSignalRecord:
        async with self._database.session_factory() as session:
            record = await session.get(DerivedSignalRecord, signal_id)
            if record is None:
                raise KeyError(signal_id)
            record.status = status
            record.status_updated_at = _now()
            await session.commit()
            return record

    async def list_case_links(self, signal_id: str) -> list[str]:
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(DerivedSignalCaseLinkRecord.case_id).where(
                    DerivedSignalCaseLinkRecord.signal_id == signal_id
                )
            )
            return [row[0] for row in rows.all()]

    async def replace_case_links(
        self, signal_id: str, case_ids: Sequence[str]
    ) -> None:
        """全量替换 Case links（先删后插，同一事务）。"""
        unique = sorted(set(case_ids))
        async with self._database.session_factory() as session:
            await session.execute(
                update(DerivedSignalRecord)
                .where(DerivedSignalRecord.id == signal_id)
                .values(related_case_ids_json=list(unique))
            )
            existing = (
                await session.scalars(
                    select(DerivedSignalCaseLinkRecord.case_id).where(
                        DerivedSignalCaseLinkRecord.signal_id == signal_id
                    )
                )
            ).all()
            existing_set = set(existing)
            for case_id in unique:
                if case_id not in existing_set:
                    session.add(
                        DerivedSignalCaseLinkRecord(
                            signal_id=signal_id, case_id=case_id
                        )
                    )
            await session.execute(
                DerivedSignalCaseLinkRecord.__table__.delete().where(
                    DerivedSignalCaseLinkRecord.signal_id == signal_id,
                    DerivedSignalCaseLinkRecord.case_id.not_in(unique)
                    if unique
                    else DerivedSignalCaseLinkRecord.case_id.isnot(None),
                )
            )
            await session.commit()
