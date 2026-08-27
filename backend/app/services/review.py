"""分层人工调查与裁决工作台（09）。

统一审核状态机与决策规则：

- 状态：unreviewed / in_review / accepted / rejected / needs_more_evidence /
  superseded。
- 决策：approved（批准）、rejected（拒绝）、edited_approval（编辑后批准）、
  more_evidence（退回补证）、revoked（撤销）。
- 决策记录追加写；撤销/覆盖通过 supersede 指向新记录，禁止覆盖历史。
- 原始 Evidence 不可编辑；只允许修改标签/相关性/纳入状态（structured_patch）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

REVIEW_STATUSES = (
    "unreviewed",
    "in_review",
    "accepted",
    "rejected",
    "needs_more_evidence",
    "superseded",
)

DECISIONS = ("approved", "rejected", "edited_approval", "more_evidence", "revoked")

# decision -> 目标状态
DECISION_TO_STATUS: dict[str, str] = {
    "approved": "accepted",
    "rejected": "rejected",
    "edited_approval": "accepted",
    "more_evidence": "needs_more_evidence",
    "revoked": "unreviewed",
}

# 合法状态转移
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "unreviewed": {"in_review", "accepted", "rejected"},
    "in_review": {"accepted", "rejected", "needs_more_evidence", "unreviewed"},
    "accepted": {"in_review", "superseded"},
    "rejected": {"in_review", "superseded"},
    "needs_more_evidence": {"in_review", "superseded"},
    "superseded": {"in_review"},
}

# 对象类型白名单（09 文档：七类调查对象）。
OBJECT_TYPES = (
    "evidence",
    "claim",
    "propagation_edge",
    "alignment_candidate",
    "risk_assessment",
    "hypothesis",
    "report_conclusion",
)

# Evidence 原始内容不可编辑；允许的 patch 键。
_EVIDENCE_ALLOWED_PATCH_KEYS = {"tags", "relevance", "reliability", "included", "stance"}


class ReviewStateError(ValueError):
    """非法状态转移或非法决策。"""


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    decision: str
    reason: str
    actor: str = "local_operator"
    structured_patch: dict[str, Any] | None = None

    def validate(self, *, object_type: str, current_status: str) -> None:
        if self.decision not in DECISIONS:
            raise ReviewStateError(f"unknown decision {self.decision!r}")
        target = DECISION_TO_STATUS[self.decision]
        if target not in _VALID_TRANSITIONS.get(current_status, set()):
            raise ReviewStateError(
                f"状态转移非法: {current_status} -> {target}（decision={self.decision}）"
            )
        if self.decision == "edited_approval" and not self.structured_patch:
            raise ReviewStateError("edited_approval 必须携带 structured_patch")
        if not self.reason and self.decision in {"rejected", "edited_approval"}:
            raise ReviewStateError("rejected/edited_approval 必须填写理由")
        if object_type == "evidence":
            self._validate_evidence_patch()

    def _validate_evidence_patch(self) -> None:
        patch = self.structured_patch or {}
        unknown = set(patch) - _EVIDENCE_ALLOWED_PATCH_KEYS
        if unknown:
            raise ReviewStateError(
                f"Evidence 只允许修改标签/相关性/可靠性/纳入状态/立场，"
                f"未知键: {sorted(unknown)}"
            )


def validate_transition(current: str, target: str) -> None:
    if current not in REVIEW_STATUSES:
        raise ReviewStateError(f"unknown status {current!r}")
    if target not in _VALID_TRANSITIONS.get(current, set()):
        raise ReviewStateError(f"状态转移非法: {current} -> {target}")


def apply_decision(current_status: str, decision: str) -> str:
    """返回决策后的目标状态（不做对象校验，供调用方组合）。"""
    if decision not in DECISIONS:
        raise ReviewStateError(f"unknown decision {decision!r}")
    target = DECISION_TO_STATUS[decision]
    validate_transition(current_status, target)
    return target
