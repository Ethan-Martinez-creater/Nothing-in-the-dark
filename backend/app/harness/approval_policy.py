"""Rules for in-run approvals: crawl-scope expansion, budget, high-cost tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any

HIGH_COST_YUAN = 1.0
DEFAULT_CRAWL_LIMIT = 150
DEFAULT_PER_DAY_LIMIT = 150
DEFAULT_COMMENT_LIMIT = 10
MAX_KEYWORD_GROUPS = 3
MAX_UPSTREAM_FETCH_PER_KEYWORD = 600


def crawl_scope(arguments: dict[str, Any]) -> dict[str, Any]:
    time_range = arguments.get("time_range") or {}
    if not isinstance(time_range, dict):
        time_range = {}
    platforms = [
        str(item) for item in (arguments.get("platforms") or []) if item
    ]
    try:
        limit = int(arguments.get("limit_per_platform") or DEFAULT_CRAWL_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_CRAWL_LIMIT
    try:
        per_day_limit = int(arguments.get("per_day_limit") or DEFAULT_PER_DAY_LIMIT)
    except (TypeError, ValueError):
        per_day_limit = DEFAULT_PER_DAY_LIMIT
    try:
        comment_limit = int(arguments.get("comment_limit") or DEFAULT_COMMENT_LIMIT)
    except (TypeError, ValueError):
        comment_limit = DEFAULT_COMMENT_LIMIT
    start = _parse_bound(str(time_range.get("start") or ""))
    end = _parse_bound(str(time_range.get("end") or ""))
    days = (
        max((end.date() - start.date()).days + 1, 1)
        if start is not None and end is not None
        else 1
    )
    effective_daily_limit = min(max(limit, 1), max(per_day_limit, 1))
    upstream_per_keyword = min(
        effective_daily_limit * days,
        MAX_UPSTREAM_FETCH_PER_KEYWORD,
    )
    return {
        "platforms": sorted(platforms),
        "start": str(time_range.get("start") or ""),
        "end": str(time_range.get("end") or ""),
        "limit": max(limit, 1),
        "per_day_limit": max(per_day_limit, 1),
        "comment_limit": max(comment_limit, 0),
        "keyword_groups_max": MAX_KEYWORD_GROUPS,
        "upstream_candidate_limit_per_keyword": upstream_per_keyword,
        "upstream_candidate_limit_per_platform": (
            upstream_per_keyword * MAX_KEYWORD_GROUPS
        ),
    }


def _parse_bound(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def crawl_scope_expanded(approved: dict[str, Any], requested: dict[str, Any]) -> bool:
    """True when the new crawl asks for more than the last approved scope."""
    extra_platforms = set(requested.get("platforms") or []) - set(
        approved.get("platforms") or []
    )
    if extra_platforms:
        return True
    if int(requested.get("limit") or DEFAULT_CRAWL_LIMIT) > int(
        approved.get("limit") or DEFAULT_CRAWL_LIMIT
    ):
        return True
    if int(requested.get("per_day_limit") or DEFAULT_PER_DAY_LIMIT) > int(
        approved.get("per_day_limit") or DEFAULT_PER_DAY_LIMIT
    ):
        return True
    if int(requested.get("comment_limit") or DEFAULT_COMMENT_LIMIT) > int(
        approved.get("comment_limit") or DEFAULT_COMMENT_LIMIT
    ):
        return True
    if int(requested.get("upstream_candidate_limit_per_platform") or 0) > int(
        approved.get("upstream_candidate_limit_per_platform") or 0
    ):
        return True
    approved_start = _parse_bound(str(approved.get("start") or ""))
    requested_start = _parse_bound(str(requested.get("start") or ""))
    if requested_start is None and approved_start is not None:
        return True
    if (
        requested_start is not None
        and approved_start is not None
        and requested_start < approved_start
    ):
        return True
    approved_end = _parse_bound(str(approved.get("end") or ""))
    requested_end = _parse_bound(str(requested.get("end") or ""))
    if requested_end is None and approved_end is not None:
        return True
    if (
        requested_end is not None
        and approved_end is not None
        and requested_end > approved_end
    ):
        return True
    return False


def effective_max_cost(definition_max: float, metadata: dict[str, Any] | None) -> float:
    override = (metadata or {}).get("max_cost_override")
    try:
        if override is not None:
            return max(float(override), definition_max)
    except (TypeError, ValueError):
        pass
    return definition_max


def budget_approval_needed(
    current_cost: float,
    *,
    max_cost: float,
    already_approved: bool,
) -> bool:
    if already_approved:
        return False
    return current_cost >= max_cost


def high_cost_tool(estimated_cost: float) -> bool:
    return float(estimated_cost or 0) >= HIGH_COST_YUAN

# ---------------------------------------------------------------------------
# M21: 广义人工介入——统一审批类型、风险策略与状态机。
# ---------------------------------------------------------------------------

APPROVAL_TOOL_EXECUTION = "tool_execution"
APPROVAL_BUDGET_INCREASE = "budget_increase"
APPROVAL_DATA_ACCESS = "data_access"
APPROVAL_PUBLISH = "publish_share_notify"
APPROVAL_POLICY_EXCEPTION = "policy_exception"
APPROVAL_HIGH_IMPACT = "high_impact_conclusion"

APPROVAL_TYPES: frozenset[str] = frozenset(
    {
        APPROVAL_TOOL_EXECUTION,
        APPROVAL_BUDGET_INCREASE,
        APPROVAL_DATA_ACCESS,
        APPROVAL_PUBLISH,
        APPROVAL_POLICY_EXCEPTION,
        APPROVAL_HIGH_IMPACT,
    }
)

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_CRITICAL = "critical"

RISK_LEVELS: frozenset[str] = frozenset(
    {RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL}
)

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_APPROVED_WITH_EDITS = "approved_with_edits"
APPROVAL_REJECTED = "rejected"
APPROVAL_EXPIRED = "expired"
APPROVAL_CANCELLED = "cancelled"
APPROVAL_CONSUMED = "consumed"

APPROVAL_STATES: frozenset[str] = frozenset(
    {
        APPROVAL_PENDING,
        APPROVAL_APPROVED,
        APPROVAL_APPROVED_WITH_EDITS,
        APPROVAL_REJECTED,
        APPROVAL_EXPIRED,
        APPROVAL_CANCELLED,
        APPROVAL_CONSUMED,
    }
)

_APPROVAL_FROM = {
    APPROVAL_PENDING: frozenset(
        {
            APPROVAL_APPROVED,
            APPROVAL_APPROVED_WITH_EDITS,
            APPROVAL_REJECTED,
            APPROVAL_EXPIRED,
            APPROVAL_CANCELLED,
        }
    ),
    APPROVAL_APPROVED: frozenset({APPROVAL_CONSUMED, APPROVAL_CANCELLED, APPROVAL_EXPIRED}),
    APPROVAL_APPROVED_WITH_EDITS: frozenset(
        {APPROVAL_CONSUMED, APPROVAL_CANCELLED, APPROVAL_EXPIRED}
    ),
    APPROVAL_REJECTED: frozenset(),
    APPROVAL_EXPIRED: frozenset(),
    APPROVAL_CANCELLED: frozenset(),
    APPROVAL_CONSUMED: frozenset(),
}


def validate_approval_transition(current: str, target: str) -> str:
    """M21: 审批决策只消费一次；重复决策以幂等方式拒绝。"""
    if current not in APPROVAL_STATES or target not in APPROVAL_STATES:
        raise ValueError("Unknown approval state: " + current + "/" + target)
    if target not in _APPROVAL_FROM[current]:
        raise ValueError("Illegal approval transition: " + current + " -> " + target)
    return target


def approval_is_terminal(status: str) -> bool:
    return status in {APPROVAL_REJECTED, APPROVAL_EXPIRED, APPROVAL_CANCELLED, APPROVAL_CONSUMED}


class ApprovalRequest:
    """一次待审批动作的完整上下文（脱敏预览，不含秘密）。"""

    def __init__(
        self,
        *,
        actor: str,
        case_id: str,
        tool: str,
        approval_type: str,
        risk_level: str = RISK_HIGH,
        scope: str = "case",
        requested_action: str = "",
        arguments_summary: str = "",
        redacted_preview: str = "",
        risk_signals: list[str] | None = None,
        allowed_decisions: list[str] | None = None,
        expires_at=None,
        reason: str = "",
        max_cost: float | None = None,
    ) -> None:
        self.actor = actor
        self.case_id = case_id
        self.tool = tool
        self.approval_type = approval_type
        self.risk_level = risk_level
        self.scope = scope
        self.requested_action = requested_action
        self.arguments_summary = arguments_summary
        self.redacted_preview = redacted_preview
        self.risk_signals = list(risk_signals or [])
        self.allowed_decisions = list(allowed_decisions or [])
        self.expires_at = expires_at
        self.reason = reason
        self.max_cost = max_cost

    def to_dict(self) -> dict[str, object]:
        return {
            "actor": self.actor,
            "case_id": self.case_id,
            "tool": self.tool,
            "approval_type": self.approval_type,
            "risk_level": self.risk_level,
            "scope": self.scope,
            "requested_action": self.requested_action,
            "arguments_summary": self.arguments_summary[:500],
            "redacted_preview": self.redacted_preview[:2000],
            "risk_signals": self.risk_signals[:20],
            "allowed_decisions": self.allowed_decisions,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "reason": self.reason,
            "max_cost": self.max_cost,
        }


class ApprovalPolicyDecision:
    """策略判定：auto_approve / require_approval / deny。"""

    def __init__(
        self,
        verdict: str,
        reason: str = "",
        policy_version: str = "1.0",
        approval_type: str = APPROVAL_TOOL_EXECUTION,
        risk_level: str = RISK_LOW,
    ) -> None:
        self.verdict = verdict
        self.reason = reason
        self.policy_version = policy_version
        self.approval_type = approval_type
        self.risk_level = risk_level

    @property
    def requires_approval(self) -> bool:
        return self.verdict == "require_approval"

    def to_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "policy_version": self.policy_version,
            "approval_type": self.approval_type,
            "risk_level": self.risk_level,
        }


class ApprovalPolicyEngine:
    """M21: 统一审批决策；默认 fail closed。

    低风险只读动作可自动批准并记录策略版本；高风险/外部副作用/预算
    扩展/数据访问/发布/策略例外一律 require_approval。策略修改必须
    单独版本化，模型不得自动放宽。
    """

    POLICY_VERSION = "1.0"

    #: 低风险只读工具白名单（自动批准，记录策略版本）。
    _AUTO_APPROVE_READONLY_TOOLS = frozenset(
        {
            "load_skill",
            "search_social_evidence",
            "query_claims",
            "query_evidence",
            "query_propagation",
            "get_artifact",
        }
    )

    def __init__(
        self,
        *,
        default_risk: str = RISK_HIGH,
        auto_approve_readonly: bool = True,
        policy_version: str = POLICY_VERSION,
    ) -> None:
        self.default_risk = default_risk
        self.auto_approve_readonly = auto_approve_readonly
        self.policy_version = policy_version

    def classify_tool(
        self,
        tool_name: str,
        *,
        side_effect: str = "none",
        estimated_cost: float = 0,
    ) -> tuple[str, str]:
        """按工具副作用/成本映射到审批类型与风险等级。"""
        if tool_name in {"notify_external", "share_result", "publish_report"}:
            return APPROVAL_PUBLISH, RISK_CRITICAL
        if side_effect in {"external_read", "external_write"}:
            return APPROVAL_TOOL_EXECUTION, RISK_HIGH
        if side_effect == "database_write" and tool_name == "write_case_memory":
            return APPROVAL_TOOL_EXECUTION, RISK_MEDIUM
        if high_cost_tool(estimated_cost):
            return APPROVAL_BUDGET_INCREASE, RISK_HIGH
        return APPROVAL_TOOL_EXECUTION, self.default_risk

    def decide(
        self,
        request: ApprovalRequest,
    ) -> ApprovalPolicyDecision:
        """默认 fail closed：无法分类的动作需要审批。"""
        if request.approval_type not in APPROVAL_TYPES:
            return ApprovalPolicyDecision(
                "require_approval",
                "未知审批类型，默认需要人工审批",
                policy_version=self.policy_version,
                approval_type=request.approval_type,
                risk_level=RISK_CRITICAL,
            )
        # 策略例外从不自动批准。
        if request.approval_type == APPROVAL_POLICY_EXCEPTION:
            return ApprovalPolicyDecision(
                "require_approval",
                "策略例外必须人工审批",
                policy_version=self.policy_version,
                approval_type=request.approval_type,
                risk_level=RISK_CRITICAL,
            )
        # 发布/外部副作用从不自动批准。
        if request.approval_type in {APPROVAL_PUBLISH, APPROVAL_DATA_ACCESS}:
            return ApprovalPolicyDecision(
                "require_approval",
                "发布/敏感数据访问必须人工审批",
                policy_version=self.policy_version,
                approval_type=request.approval_type,
                risk_level=RISK_CRITICAL,
            )
        if request.risk_level == RISK_CRITICAL:
            return ApprovalPolicyDecision(
                "require_approval",
                "关键风险动作必须人工审批",
                policy_version=self.policy_version,
                approval_type=request.approval_type,
                risk_level=RISK_CRITICAL,
            )
        # 低风险只读自动批准（记录策略版本）。
        if (
            self.auto_approve_readonly
            and request.risk_level == RISK_LOW
            and request.tool in self._AUTO_APPROVE_READONLY_TOOLS
            and request.approval_type == APPROVAL_TOOL_EXECUTION
        ):
            return ApprovalPolicyDecision(
                "auto_approve",
                "低风险只读工具自动批准",
                policy_version=self.policy_version,
                approval_type=request.approval_type,
                risk_level=RISK_LOW,
            )
        return ApprovalPolicyDecision(
            "require_approval",
            request.reason or "动作需要人工审批",
            policy_version=self.policy_version,
            approval_type=request.approval_type,
            risk_level=request.risk_level,
        )

    def default_allowed_decisions(
        self,
        approval_type: str,
    ) -> list[str]:
        if approval_type == APPROVAL_PUBLISH:
            return ["approve", "reject", "cancel"]
        if approval_type == APPROVAL_POLICY_EXCEPTION:
            return ["approve", "reject", "cancel"]
        return ["approve", "edit_and_approve", "reject", "cancel"]

    @staticmethod
    def default_expiry_hours(risk_level: str) -> float:
        return {
            RISK_LOW: 72.0,
            RISK_MEDIUM: 48.0,
            RISK_HIGH: 24.0,
            RISK_CRITICAL: 8.0,
        }.get(risk_level, 24.0)
