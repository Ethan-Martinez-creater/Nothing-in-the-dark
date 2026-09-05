"""M6 + V3 §57: Global Signals service — Monitor Alert + Derived Signal 合流。

Monitor 路径不复制 alert 状态机（委托 MonitorRepository.set_alert_status）；
Derived 路径委托 DerivedSignalRepository.set_status。list_signals 在
Service 统一 merge + deterministic sort（§57），不只在前端排序。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import select

from app.core.errors import ApplicationError, ResourceNotFoundError
from app.infrastructure.database.derived_signal_repository import (
    DerivedSignalRepository,
)
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import CaseRecord
from app.infrastructure.database.monitor_repository import (
    MonitorRepository,
    SignalRow,
)
from app.schemas.signals import (
    SIGNAL_TITLE_BY_TYPE,
    SIGNAL_TYPE_BY_RULE,
    SOURCE_LABELS,
    SignalResponse,
)

logger = logging.getLogger(__name__)

VALID_SIGNAL_STATUSES = ("open", "acknowledged", "resolved", "suppressed")

_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


class SignalService:
    def __init__(
        self,
        database: Database,
        repository: MonitorRepository,
        derived_repository: DerivedSignalRepository | None = None,
    ) -> None:
        self._database = database
        self._monitors = repository
        self._derived = derived_repository

    async def list_signals(
        self,
        *,
        statuses: Sequence[str] | None = None,
        severity: str | None = None,
        case_id: str | None = None,
        signal_type: str | None = None,
        source_type: str | None = None,
        detector_active: bool | None = None,
        limit: int = 100,
    ) -> list[SignalResponse]:
        # signal_type 由前端视角命名（volume_spike 等），回查 rule_type
        rule_type: str | None = None
        if signal_type:
            rule_type = next(
                (key for key, value in SIGNAL_TYPE_BY_RULE.items() if value == signal_type),
                signal_type,
            )
        rows = await self._monitors.list_signal_rows(
            statuses=statuses,
            severity=severity,
            case_id=case_id,
            rule_type=rule_type,
            limit=limit * 2 if limit else None,
        )
        monitor_signals = [self._to_signal(row) for row in rows]

        derived_signals: list[SignalResponse] = []
        if self._derived is not None:
            if case_id:
                records = await self._derived.list_for_case(
                    case_id,
                    statuses=statuses,
                    severity=severity,
                    signal_type=signal_type,
                    detector_active=detector_active,
                    limit=max(limit * 2, 200) if limit else 200,
                )
            else:
                records = await self._derived.list(
                    statuses=statuses,
                    severity=severity,
                    signal_type=signal_type,
                    source_type=source_type,
                    detector_active=detector_active,
                    limit=max(limit * 2, 200) if limit else 200,
                )
            derived_signals = await self._to_derived_signals(records)

        merged = [*monitor_signals, *derived_signals]
        if source_type:
            merged = [signal for signal in merged if signal.source_type == source_type]
        # §57 server-side deterministic sort：severity rank ASC → detected_at DESC → id ASC
        merged.sort(
            key=lambda signal: (
                _SEVERITY_RANK.get(signal.severity, 3),
                -(signal.detected_at.timestamp() if signal.detected_at else 0),
                signal.id,
            )
        )
        return merged[:limit]

    async def get_signal(self, signal_id: str) -> SignalResponse:
        # §57.1 lookup 顺序：Monitor → Derived → 404；同 ID 撞源记 error
        monitor = await self._find_monitor(signal_id)
        derived = await self._derived.get(signal_id) if self._derived is not None else None
        if monitor is not None and derived is not None:
            logger.error(
                "ambiguous signal id %s exists in both monitor and derived sources",
                signal_id,
            )
            # 双源同 ID 时按既有行为优先 Monitor（避免破坏旧调用方）
            return self._to_signal(monitor)
        if monitor is not None:
            return self._to_signal(monitor)
        if derived is not None:
            return (await self._to_derived_signals([derived]))[0]
        error = ResourceNotFoundError("signal", signal_id)
        error.code = "signal_not_found"
        raise error

    async def change_status(
        self, signal_id: str, action: str, *, actor: str = "local_operator"
    ) -> SignalResponse:
        action_to_status = {
            "acknowledge": "acknowledged",
            "resolve": "resolved",
            "suppress": "suppressed",
        }
        status = action_to_status.get(action)
        if status is None:
            raise ApplicationError(
                f"unknown signal action '{action}'", code="signal_not_found"
            )
        monitor = await self._find_monitor(signal_id)
        derived = await self._derived.get(signal_id) if self._derived is not None else None
        if monitor is not None and derived is not None:
            logger.error(
                "refusing ambiguous signal mutation %s (both monitor and derived)",
                signal_id,
            )
            error = ResourceNotFoundError("signal", signal_id)
            error.code = "signal_not_found"
            raise error
        if monitor is not None:
            await self._monitors.set_alert_status(signal_id, status, by=actor)
            # 重新读取，确保返回变更后的状态（monitor row 是变更前快照）
            return await self.get_signal(signal_id)
        if derived is not None:
            record = await self._derived.set_status(signal_id, status)
            return (await self._to_derived_signals([record]))[0]
        error = ResourceNotFoundError("signal", signal_id)
        error.code = "signal_not_found"
        raise error

    # ------------------------------------------------------------------

    async def _find_monitor(self, signal_id: str) -> SignalRow | None:
        rows = await self._monitors.list_signal_rows(limit=10_000)
        for row in rows:
            if row.alert.id == signal_id:
                return row
        return None

    async def _to_derived_signals(
        self, records: Sequence[object]
    ) -> list[SignalResponse]:
        if not records:
            return []
        case_ids: set[str] = set()
        for record in records:
            primary = getattr(record, "case_id", None)
            if primary:
                case_ids.add(str(primary))
            for case_id in (getattr(record, "related_case_ids_json", None) or []):
                case_ids.add(str(case_id))
        titles = await self._load_case_titles(sorted(case_ids))
        return [self._to_derived_signal(record, titles) for record in records]

    async def _load_case_titles(self, case_ids: Sequence[str]) -> dict[str, str]:
        if not case_ids:
            return {}
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(CaseRecord.id, CaseRecord.title).where(
                    CaseRecord.id.in_(tuple(case_ids))
                )
            )
            return {row_id: title for row_id, title in rows.all()}

    def _to_derived_signal(
        self, record: object, titles: dict[str, str]
    ) -> SignalResponse:
        related = list(getattr(record, "related_case_ids_json", None) or [])
        primary = str(getattr(record, "case_id", "") or "")
        detected = getattr(record, "last_seen_at", None) or getattr(
            record, "first_seen_at", None
        )
        signal_type = str(getattr(record, "signal_type", ""))
        # Rework R7：Derived Signal 的 evidence_refs 原样透传（截断到 50 条），
        # 供前端 Evidence section 展示 entity_id / sha256 / relation_type 等。
        evidence_items = list(getattr(record, "evidence_refs_json", None) or [])[:50]
        return SignalResponse(
            id=str(getattr(record, "id", "")),
            source_type=str(getattr(record, "source_type", "derived")),
            source_id=str(getattr(record, "source_id", "")),
            case_id=primary,
            case_title=titles.get(primary, primary),
            signal_type=signal_type,
            severity=str(getattr(record, "severity", "info")),
            status=str(getattr(record, "status", "open")),
            title=str(getattr(record, "title", "")),
            why_it_matters=str(getattr(record, "why_it_matters", "")),
            confidence=getattr(record, "confidence", None),
            evidence_refs={"items": evidence_items},
            trigger_count=int(getattr(record, "occurrence_count", 1) or 1),
            first_seen_at=getattr(record, "first_seen_at", None),
            detected_at=detected,
            updated_at=getattr(record, "updated_at", None),
            related_case_ids=related,
            source_label=SOURCE_LABELS.get(signal_type, signal_type),
            detector_version=getattr(record, "detector_version", None),
            detector_active=getattr(record, "detector_active", None),
        )

    def _to_signal(self, row: SignalRow) -> SignalResponse:
        alert = row.alert
        signal_type = SIGNAL_TYPE_BY_RULE.get(row.rule_type, row.rule_type)
        title = SIGNAL_TITLE_BY_TYPE.get(signal_type, "监测触发信号")
        metric = alert.metric_snapshot if isinstance(alert.metric_snapshot, dict) else {}
        explanation = (alert.explanation or "").strip()
        if not explanation:
            explanation = f"规则 {row.rule_type} 触发（监测「{row.monitor_name}」）"
        confidence = metric.get("confidence")
        return SignalResponse(
            id=alert.id,
            source_type="monitor_alert",
            source_id=alert.id,
            case_id=row.case_id,
            case_title=row.case_title,
            signal_type=signal_type,
            severity=row.severity,
            status=alert.status,
            title=title,
            why_it_matters=explanation,
            confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            evidence_refs=alert.evidence_refs if isinstance(alert.evidence_refs, dict) else {},
            trigger_count=alert.trigger_count,
            first_seen_at=alert.first_seen_at,
            detected_at=alert.first_seen_at or alert.last_seen_at,
            updated_at=alert.last_seen_at,
        )
