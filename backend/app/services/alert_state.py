"""C4: Alert operational state machine（Monitor 与 Signal 共用）。

状态流：open -> acknowledged -> resolved；suppressed 可从 open/
acknowledged/resolved 进入（monitors 路由既有行为的单一事实来源）。
纯 domain validator：不访问数据库，供路由、服务与仓储层复用。
"""

from __future__ import annotations

from app.core.errors import ApplicationError

VALID_ALERT_TRANSITIONS: dict[str, set[str]] = {
    "acknowledged": {"open"},
    "resolved": {"open", "acknowledged"},
    "suppressed": {"open", "acknowledged", "resolved"},
}

# 合法初始状态（创建时）
ALERT_INITIAL_STATUS = "open"


def validate_alert_transition(current_status: str, target_status: str) -> None:
    """校验 alert 状态迁移；非法时抛统一业务错误。"""
    allowed = VALID_ALERT_TRANSITIONS.get(target_status, set())
    if current_status not in allowed:
        raise ApplicationError(
            f"告警状态不能从 '{current_status}' 变更为 '{target_status}'",
            code="alert_status_transition_invalid",
        )
