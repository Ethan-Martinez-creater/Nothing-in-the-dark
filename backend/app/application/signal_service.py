"""M6: Global Signals service — Monitor Alert 的产品层 adapter。

不复制 alert 状态机：acknowledge/resolve/suppress 直接委托
``MonitorRepository.set_alert_status`` 的既有合法转移。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.errors import ApplicationError, ResourceNotFoundError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.monitor_repository import (
    MonitorRepository,
    SignalRow,
)
from app.schemas.signals import SIGNAL_TITLE_BY_TYPE, SIGNAL_TYPE_BY_RULE, SignalResponse

VALID_SIGNAL_STATUSES = ("open", "acknowledged", "resolved", "suppressed")


class SignalService:
    def __init__(self, database: Database, repository: MonitorRepository) -> None:
        self._database = database
        self._monitors = repository

    async def list_signals(
        self,
        *,
        statuses: Sequence[str] | None = None,
        severity: str | None = None,
        case_id: str | None = None,
        signal_type: str | None = None,
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
            limit=limit,
        )
        return [self._to_signal(row) for row in rows]

    async def get_signal(self, signal_id: str) -> SignalResponse:
        # 单条查询：join 全量后按 alert id 过滤（signals 数量有界）
        rows = await self._monitors.list_signal_rows(limit=10_000)
        for row in rows:
            if row.alert.id == signal_id:
                return self._to_signal(row)
        # 404 复用 ResourceNotFoundError，code 遵循计划书错误码表
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
        await self._monitors.set_alert_status(signal_id, status, by=actor)
        return await self.get_signal(signal_id)

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
