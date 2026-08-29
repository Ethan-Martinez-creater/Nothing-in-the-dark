"""Monitor & alert persistence (01 持续监测).

独立仓储，避免继续扩大 ApplicationRepository；数据访问风格与现有仓储一致
（session_factory + select + commit）。execution 使用 (monitor_id, scheduled_at)
唯一约束保证幂等，claim_execution 用 FOR UPDATE SKIP LOCKED 原子领取。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ResourceNotFoundError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    AlertOccurrenceRecord,
    AlertRuleRecord,
    MonitorCursorRecord,
    MonitorDefinitionRecord,
    MonitorExecutionRecord,
)


def _now() -> datetime:
    return datetime.now(UTC)


class MonitorRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    # ---- monitor definitions -------------------------------------------

    async def create_monitor(
        self,
        *,
        case_id: str,
        name: str,
        schedule_type: str = "interval",
        interval_seconds: int | None = None,
        cron: str | None = None,
        timezone: str = "Asia/Shanghai",
        query_spec: dict[str, object] | None = None,
        platforms: list[str] | None = None,
        account_watchlist: list[dict[str, object]] | None = None,
        lookback_seconds: int = 3600,
        analysis_policy: dict[str, object] | None = None,
    ) -> MonitorDefinitionRecord:
        record = MonitorDefinitionRecord(
            case_id=case_id,
            name=name,
            schedule_type=schedule_type,
            interval_seconds=interval_seconds,
            cron=cron,
            timezone=timezone,
            query_spec=query_spec or {},
            platforms=platforms or [],
            account_watchlist=account_watchlist or [],
            lookback_seconds=lookback_seconds,
            analysis_policy=analysis_policy or {},
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_monitor(self, monitor_id: str) -> MonitorDefinitionRecord:
        async with self._database.session_factory() as session:
            record = await session.get(MonitorDefinitionRecord, monitor_id)
            if record is None:
                raise ResourceNotFoundError("monitor", monitor_id)
            return record

    async def list_monitors(
        self,
        *,
        case_id: str | None = None,
        enabled: bool | None = None,
    ) -> Sequence[MonitorDefinitionRecord]:
        query = select(MonitorDefinitionRecord)
        if case_id is not None:
            query = query.where(MonitorDefinitionRecord.case_id == case_id)
        if enabled is not None:
            query = query.where(MonitorDefinitionRecord.enabled.is_(enabled))
        query = query.order_by(MonitorDefinitionRecord.created_at.asc())
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def update_monitor(
        self,
        monitor_id: str,
        *,
        version: int,
        name: str | None = None,
        schedule_type: str | None = None,
        interval_seconds: int | None = None,
        cron: str | None = None,
        timezone: str | None = None,
        query_spec: dict[str, object] | None = None,
        platforms: list[str] | None = None,
        account_watchlist: list[dict[str, object]] | None = None,
        lookback_seconds: int | None = None,
        analysis_policy: dict[str, object] | None = None,
    ) -> MonitorDefinitionRecord:
        """乐观锁更新：条件 UPDATE 保证 version 原子比较与自增。"""
        await self.get_monitor(monitor_id)  # 404 for unknown monitor
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name
        if schedule_type is not None:
            values["schedule_type"] = schedule_type
        if interval_seconds is not None:
            values["interval_seconds"] = interval_seconds
        if cron is not None:
            values["cron"] = cron
        if timezone is not None:
            values["timezone"] = timezone
        if query_spec is not None:
            values["query_spec"] = query_spec
        if platforms is not None:
            values["platforms"] = platforms
        if account_watchlist is not None:
            values["account_watchlist"] = account_watchlist
        if lookback_seconds is not None:
            values["lookback_seconds"] = lookback_seconds
        if analysis_policy is not None:
            values["analysis_policy"] = analysis_policy
        values["updated_at"] = _now()
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(MonitorDefinitionRecord)
                .where(
                    MonitorDefinitionRecord.id == monitor_id,
                    MonitorDefinitionRecord.version == version,
                )
                .values(**values, version=MonitorDefinitionRecord.version + 1)
            )
            await session.commit()
            if result.rowcount == 0:
                raise ResourceNotFoundError(
                    "monitor (version conflict)",
                    f"{monitor_id}@v{version}",
                )
        return await self.get_monitor(monitor_id)

    async def set_monitor_enabled(
        self,
        monitor_id: str,
        enabled: bool,
    ) -> MonitorDefinitionRecord:
        await self.get_monitor(monitor_id)  # 404 for unknown monitor
        async with self._database.session_factory() as session:
            current = await session.get(MonitorDefinitionRecord, monitor_id)
            assert current is not None
            current.enabled = enabled
            current.updated_at = _now()
            await session.commit()
            await session.refresh(current)
        return current

    async def delete_monitor(self, monitor_id: str) -> None:
        """删除监测定义，先按依赖顺序清理子表。"""
        await self.get_monitor(monitor_id)
        async with self._database.session_factory() as session:
            await session.execute(
                delete(AlertOccurrenceRecord).where(
                    AlertOccurrenceRecord.monitor_id == monitor_id
                )
            )
            await session.execute(
                delete(AlertRuleRecord).where(
                    AlertRuleRecord.monitor_id == monitor_id
                )
            )
            await session.execute(
                delete(MonitorCursorRecord).where(
                    MonitorCursorRecord.monitor_id == monitor_id
                )
            )
            await session.execute(
                delete(MonitorExecutionRecord).where(
                    MonitorExecutionRecord.monitor_id == monitor_id
                )
            )
            current = await session.get(MonitorDefinitionRecord, monitor_id)
            assert current is not None
            await session.delete(current)
            await session.commit()

    # ---- cursors --------------------------------------------------------

    async def get_cursor(
        self,
        monitor_id: str,
        platform: str,
    ) -> MonitorCursorRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(MonitorCursorRecord).where(
                    MonitorCursorRecord.monitor_id == monitor_id,
                    MonitorCursorRecord.platform == platform,
                )
            )

    async def list_cursors(self, monitor_id: str) -> Sequence[MonitorCursorRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(MonitorCursorRecord).where(
                        MonitorCursorRecord.monitor_id == monitor_id
                    )
                )
            ).all()

    async def upsert_cursor(
        self,
        *,
        monitor_id: str,
        platform: str,
        cursor_payload: dict[str, object] | None = None,
        last_window_end: datetime | None = None,
    ) -> MonitorCursorRecord:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(MonitorCursorRecord).where(
                    MonitorCursorRecord.monitor_id == monitor_id,
                    MonitorCursorRecord.platform == platform,
                )
            )
            if record is None:
                record = MonitorCursorRecord(
                    monitor_id=monitor_id,
                    platform=platform,
                    cursor_payload=cursor_payload or {},
                    last_window_end=last_window_end,
                )
                session.add(record)
            else:
                record.cursor_payload = cursor_payload or record.cursor_payload
                record.last_window_end = last_window_end
                record.last_success_at = _now()
                record.consecutive_failures = 0
                record.lease_owner = None
                record.lease_expires_at = None
            await session.commit()
            await session.refresh(record)
        return record

    async def record_cursor_failure(
        self,
        monitor_id: str,
        platform: str,
    ) -> MonitorCursorRecord | None:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(MonitorCursorRecord).where(
                    MonitorCursorRecord.monitor_id == monitor_id,
                    MonitorCursorRecord.platform == platform,
                )
            )
            if record is None:
                record = MonitorCursorRecord(
                    monitor_id=monitor_id,
                    platform=platform,
                )
                session.add(record)
            record.consecutive_failures += 1
            record.lease_owner = None
            record.lease_expires_at = None
            await session.commit()
            await session.refresh(record)
        return record

    # ---- executions -----------------------------------------------------

    async def create_execution(
        self,
        *,
        monitor_id: str,
        scheduled_at: datetime,
        idempotency_key: str | None = None,
    ) -> MonitorExecutionRecord | None:
        """幂等创建执行；已存在时返回 None（靠唯一约束去重）。"""
        record = MonitorExecutionRecord(
            monitor_id=monitor_id,
            scheduled_at=scheduled_at,
            status="scheduled",
            idempotency_key=idempotency_key,
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

    async def get_latest_scheduled_at(
        self,
        monitor_id: str,
    ) -> datetime | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(MonitorExecutionRecord.scheduled_at)
                .where(MonitorExecutionRecord.monitor_id == monitor_id)
                .order_by(MonitorExecutionRecord.scheduled_at.desc())
                .limit(1)
            )

    async def get_execution_by_idempotency_key(
        self,
        monitor_id: str,
        idempotency_key: str,
    ) -> MonitorExecutionRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(MonitorExecutionRecord).where(
                    MonitorExecutionRecord.monitor_id == monitor_id,
                    MonitorExecutionRecord.idempotency_key == idempotency_key,
                )
            )

    async def get_execution_by_scheduled_at(
        self,
        monitor_id: str,
        scheduled_at: datetime,
    ) -> MonitorExecutionRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(MonitorExecutionRecord).where(
                    MonitorExecutionRecord.monitor_id == monitor_id,
                    MonitorExecutionRecord.scheduled_at == scheduled_at,
                )
            )

    async def claim_execution(
        self,
        worker_id: str,
        lease_seconds: int,
    ) -> MonitorExecutionRecord | None:
        now = _now()
        async with self._database.session_factory() as session:
            execution_id = await session.scalar(
                select(MonitorExecutionRecord.id)
                .where(
                    MonitorExecutionRecord.status.in_(["scheduled", "running"]),
                    or_(
                        MonitorExecutionRecord.lease_expires_at.is_(None),
                        MonitorExecutionRecord.lease_expires_at < now,
                    ),
                )
                .order_by(MonitorExecutionRecord.scheduled_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if execution_id is None:
                await session.commit()
                return None
            record = await session.get(MonitorExecutionRecord, execution_id)
            assert record is not None
            if record.status == "scheduled":
                record.status = "running"
                record.started_at = now
            record.lease_owner = worker_id
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            await session.commit()
            await session.refresh(record)
            return record

    async def refresh_execution_lease(
        self,
        execution_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        """续租；仅当仍由该 worker 持有时成功，返回是否续租成功。"""
        now = _now()
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(MonitorExecutionRecord)
                .where(
                    MonitorExecutionRecord.id == execution_id,
                    MonitorExecutionRecord.lease_owner == worker_id,
                )
                .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
            )
            await session.commit()
            return result.rowcount > 0

    async def get_execution(self, execution_id: str) -> MonitorExecutionRecord:
        async with self._database.session_factory() as session:
            record = await session.get(MonitorExecutionRecord, execution_id)
            if record is None:
                raise ResourceNotFoundError("monitor execution", execution_id)
            return record


    async def update_execution_if_owner(
        self,
        execution_id: str,
        worker_id: str,
        **fields: Any,
    ) -> bool:
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(MonitorExecutionRecord)
                .where(
                    MonitorExecutionRecord.id == execution_id,
                    MonitorExecutionRecord.status == "running",
                    MonitorExecutionRecord.lease_owner == worker_id,
                )
                .values(**fields)
            )
            await session.commit()
            return result.rowcount == 1

    async def finish_execution(
        self,
        execution_id: str,
        worker_id: str,
        **fields: Any,
    ) -> bool:
        fields.setdefault("finished_at", _now())
        fields["lease_owner"] = None
        fields["lease_expires_at"] = None
        return await self.update_execution_if_owner(
            execution_id, worker_id, **fields
        )


    async def update_execution(
        self,
        execution_id: str,
        **fields: Any,
    ) -> MonitorExecutionRecord:
        async with self._database.session_factory() as session:
            record = await session.get(MonitorExecutionRecord, execution_id)
            if record is None:
                raise ResourceNotFoundError("monitor execution", execution_id)
            for key, value in fields.items():
                setattr(record, key, value)
            await session.commit()
            await session.refresh(record)
        return record

    async def list_executions(
        self,
        monitor_id: str,
        *,
        limit: int = 50,
    ) -> Sequence[MonitorExecutionRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(MonitorExecutionRecord)
                    .where(MonitorExecutionRecord.monitor_id == monitor_id)
                    .order_by(MonitorExecutionRecord.scheduled_at.desc())
                    .limit(limit)
                )
            ).all()

    async def list_recent_executions(
        self,
        monitor_id: str,
        *,
        limit: int = 20,
        statuses: tuple[str, ...] = ("succeeded", "partial"),
    ) -> Sequence[MonitorExecutionRecord]:
        """历史窗口统计（供增长率与异常检测取基线）。"""
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(MonitorExecutionRecord)
                    .where(
                        MonitorExecutionRecord.monitor_id == monitor_id,
                        MonitorExecutionRecord.status.in_(statuses),
                    )
                    .order_by(MonitorExecutionRecord.scheduled_at.desc())
                    .limit(limit)
                )
            ).all()

    # ---- rules ----------------------------------------------------------

    async def create_rule(
        self,
        *,
        monitor_id: str,
        rule_type: str,
        parameters: dict[str, object] | None = None,
        severity: str = "warning",
        cooldown_seconds: int = 3600,
        enabled: bool = True,
    ) -> AlertRuleRecord:
        record = AlertRuleRecord(
            monitor_id=monitor_id,
            rule_type=rule_type,
            parameters=parameters or {},
            severity=severity,
            cooldown_seconds=cooldown_seconds,
            enabled=enabled,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_rule(self, rule_id: str) -> AlertRuleRecord:
        async with self._database.session_factory() as session:
            record = await session.get(AlertRuleRecord, rule_id)
            if record is None:
                raise ResourceNotFoundError("alert rule", rule_id)
            return record

    async def list_rules(self, monitor_id: str) -> Sequence[AlertRuleRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(AlertRuleRecord)
                    .where(AlertRuleRecord.monitor_id == monitor_id)
                    .order_by(AlertRuleRecord.created_at.asc())
                )
            ).all()

    async def update_rule(
        self,
        rule_id: str,
        *,
        version: int,
        parameters: dict[str, object] | None = None,
        severity: str | None = None,
        cooldown_seconds: int | None = None,
        enabled: bool | None = None,
    ) -> AlertRuleRecord:
        await self.get_rule(rule_id)  # 404 for unknown rule
        values: dict[str, Any] = {}
        if parameters is not None:
            values["parameters"] = parameters
        if severity is not None:
            values["severity"] = severity
        if cooldown_seconds is not None:
            values["cooldown_seconds"] = cooldown_seconds
        if enabled is not None:
            values["enabled"] = enabled
        values["updated_at"] = _now()
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(AlertRuleRecord)
                .where(
                    AlertRuleRecord.id == rule_id,
                    AlertRuleRecord.version == version,
                )
                .values(**values, version=AlertRuleRecord.version + 1)
            )
            await session.commit()
            if result.rowcount == 0:
                raise ResourceNotFoundError("alert rule (version conflict)", rule_id)
        return await self.get_rule(rule_id)

    async def delete_rule(self, rule_id: str) -> None:
        await self.get_rule(rule_id)
        async with self._database.session_factory() as session:
            await session.execute(
                delete(AlertOccurrenceRecord).where(
                    AlertOccurrenceRecord.rule_id == rule_id
                )
            )
            current = await session.get(AlertRuleRecord, rule_id)
            assert current is not None
            await session.delete(current)
            await session.commit()

    # ---- alerts ---------------------------------------------------------

    async def upsert_alert_occurrence(
        self,
        *,
        monitor_id: str,
        rule_id: str,
        fingerprint: str,
        cooldown_bucket: str,
        severity: str,
        explanation: str,
        metric_snapshot: dict[str, object],
        evidence_refs: dict[str, object],
    ) -> tuple[AlertOccurrenceRecord, bool]:
        """合并同 (rule, fingerprint, bucket) 告警；返回 (record, created)。

        依赖唯一约束 + IntegrityError 捕获，冲突后原子累加，避免先查后插竞态。
        """
        record = AlertOccurrenceRecord(
            monitor_id=monitor_id,
            rule_id=rule_id,
            fingerprint=fingerprint,
            cooldown_bucket=cooldown_bucket,
            status="open",
            explanation=explanation,
            metric_snapshot=metric_snapshot,
            evidence_refs=evidence_refs,
            trigger_count=1,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
            else:
                await session.refresh(record)
                return record, True

        # 已存在：原子累加。
        async with self._database.session_factory() as session:
            await session.execute(
                update(AlertOccurrenceRecord)
                .where(
                    AlertOccurrenceRecord.rule_id == rule_id,
                    AlertOccurrenceRecord.fingerprint == fingerprint,
                    AlertOccurrenceRecord.cooldown_bucket == cooldown_bucket,
                )
                .values(
                    trigger_count=AlertOccurrenceRecord.trigger_count + 1,
                    last_seen_at=_now(),
                    metric_snapshot=metric_snapshot,
                    evidence_refs=evidence_refs,
                    explanation=explanation,
                )
            )
            await session.commit()
            merged = await session.scalar(
                select(AlertOccurrenceRecord).where(
                    AlertOccurrenceRecord.rule_id == rule_id,
                    AlertOccurrenceRecord.fingerprint == fingerprint,
                    AlertOccurrenceRecord.cooldown_bucket == cooldown_bucket,
                )
            )
            assert merged is not None
            if merged.status in ("resolved", "suppressed"):
                merged.status = "open"
                await session.commit()
                await session.refresh(merged)
            return merged, False

    async def get_alert(self, alert_id: str) -> AlertOccurrenceRecord:
        async with self._database.session_factory() as session:
            record = await session.get(AlertOccurrenceRecord, alert_id)
            if record is None:
                raise ResourceNotFoundError("alert", alert_id)
            return record

    async def list_alerts(
        self,
        *,
        case_id: str | None = None,
        monitor_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> Sequence[AlertOccurrenceRecord]:
        query = select(AlertOccurrenceRecord)
        if monitor_id is not None:
            query = query.where(AlertOccurrenceRecord.monitor_id == monitor_id)
        if case_id is not None:
            from app.infrastructure.database.models import MonitorDefinitionRecord as MDR

            monitor_ids = select(MDR.id).where(MDR.case_id == case_id)
            query = query.where(AlertOccurrenceRecord.monitor_id.in_(monitor_ids))
        if status is not None:
            query = query.where(AlertOccurrenceRecord.status == status)
        query = query.order_by(AlertOccurrenceRecord.last_seen_at.desc()).limit(limit)
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def set_alert_status(
        self,
        alert_id: str,
        status: str,
        *,
        by: str | None = None,
    ) -> AlertOccurrenceRecord:
        await self.get_alert(alert_id)  # 404 for unknown alert
        async with self._database.session_factory() as session:
            current = await session.get(AlertOccurrenceRecord, alert_id)
            assert current is not None
            current.status = status
            if status == "acknowledged" and by:
                current.acknowledged_by = by
                current.acknowledged_at = _now()
            current.updated_at = _now()
            await session.commit()
            await session.refresh(current)
        return current

    # ---------------- M6: Global Signals（alert join 视图，无新表） ----------------

    async def list_signal_rows(
        self,
        *,
        statuses: Sequence[str] | None = None,
        severity: str | None = None,
        case_id: str | None = None,
        rule_type: str | None = None,
        limit: int = 100,
    ) -> Sequence[SignalRow]:
        """全局 Signal 查询：alert × rule × monitor × case 单次 join。

        服务端排序：critical > warning > info，再按 last_seen_at desc。
        """
        from sqlalchemy import case as sa_case

        from app.infrastructure.database.models import (
            AlertRuleRecord,
            CaseRecord,
            MonitorDefinitionRecord,
        )

        query = (
            select(
                AlertOccurrenceRecord,
                AlertRuleRecord.rule_type,
                AlertRuleRecord.severity,
                MonitorDefinitionRecord.name,
                MonitorDefinitionRecord.case_id,
                CaseRecord.title,
            )
            .join(AlertRuleRecord, AlertRuleRecord.id == AlertOccurrenceRecord.rule_id)
            .join(
                MonitorDefinitionRecord,
                MonitorDefinitionRecord.id == AlertOccurrenceRecord.monitor_id,
            )
            .join(CaseRecord, CaseRecord.id == MonitorDefinitionRecord.case_id)
        )
        if statuses:
            query = query.where(AlertOccurrenceRecord.status.in_(list(statuses)))
        if severity:
            query = query.where(AlertRuleRecord.severity == severity)
        if case_id:
            query = query.where(MonitorDefinitionRecord.case_id == case_id)
        if rule_type:
            query = query.where(AlertRuleRecord.rule_type == rule_type)
        severity_order = sa_case(
            (AlertRuleRecord.severity == "critical", 0),
            (AlertRuleRecord.severity == "warning", 1),
            else_=2,
        )
        query = query.order_by(
            severity_order, AlertOccurrenceRecord.last_seen_at.desc()
        ).limit(limit)
        async with self._database.session_factory() as session:
            rows = (await session.execute(query)).all()
            return [
                SignalRow(
                    alert=row[0],
                    rule_type=row[1],
                    severity=row[2],
                    monitor_name=row[3],
                    case_id=row[4],
                    case_title=row[5],
                )
                for row in rows
            ]


class SignalRow:
    """M6: 全局 Signal 行（alert + rule/monitor/case 元数据）。"""

    __slots__ = (
        "alert",
        "rule_type",
        "severity",
        "monitor_name",
        "case_id",
        "case_title",
    )

    def __init__(
        self,
        *,
        alert: Any,
        rule_type: str,
        severity: str,
        monitor_name: str,
        case_id: str,
        case_title: str,
    ) -> None:
        self.alert = alert
        self.rule_type = rule_type
        self.severity = severity
        self.monitor_name = monitor_name
        self.case_id = case_id
        self.case_title = case_title
