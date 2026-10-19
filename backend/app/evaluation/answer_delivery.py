"""Structural delivery classification for investigation answers.

This module distinguishes model-produced content from runtime stop messages.
It intentionally does not judge whether a model answer is correct, complete,
or supported by its citations; every model answer remains subject to review.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

ANSWER_DELIVERY_VERSION = "answer-delivery-v1"

_RUNTIME_STOP_MARKERS = (
    "本次运行已达到工具调用上限",
    "只读执行策略阻止了写入工具",
    "工具请求包含未知工具",
)
_REFUSAL_MARKERS = (
    "无法提供",
    "无法统计",
    "无法确定",
    "无法核实",
    "无法读取",
    "无法确认",
    "不能提供",
    "不能给出",
    "不足以给出",
    "数据不可用",
    "cannot provide",
    "cannot determine",
    "cannot verify",
    "unable to provide",
    "unable to determine",
    "data is unavailable",
    "data are unavailable",
)
_COMPLETED_STATUSES = frozenset({"completed", "complete"})


def assess_answer_delivery(
    *,
    final_answer: str,
    status: str,
    task_tags: Sequence[str],
    policy_audit: Sequence[Mapping[str, Any]],
    injected_failure_count: int = 0,
) -> dict[str, Any]:
    """Classify whether a task delivered model content for human review.

    ``structural_pass`` only means a model-origin answer was delivered for
    review. It never means the investigation objective was met. Runtime-only
    fallbacks and interrupted runs fail this structural delivery check.
    """
    answer = final_answer.strip() if isinstance(final_answer, str) else ""
    normalized_status = status.strip().lower() if isinstance(status, str) else ""
    tags = frozenset(tag for tag in task_tags if isinstance(tag, str))
    events = [event for event in policy_audit if isinstance(event, Mapping)]

    finalization = _latest_finalization_outcome(events)
    has_runtime_marker = any(marker in answer for marker in _RUNTIME_STOP_MARKERS)
    origin = _answer_origin(answer, finalization, has_runtime_marker)

    if normalized_status not in _COMPLETED_STATUSES:
        delivery_status = "INTERRUPTED"
    elif not answer:
        delivery_status = "NOT_DELIVERED"
    elif origin == "RUNTIME_STOP":
        delivery_status = "NOT_DELIVERED"
    elif (
        "fault:persistent_read" in tags
        and isinstance(injected_failure_count, int)
        and not isinstance(injected_failure_count, bool)
        and injected_failure_count > 0
        and _looks_like_refusal(answer)
    ):
        delivery_status = "EXPECTED_REFUSAL_REQUIRES_REVIEW"
    else:
        delivery_status = "MODEL_ANSWER_REQUIRES_REVIEW"

    structural_pass = delivery_status in {
        "MODEL_ANSWER_REQUIRES_REVIEW",
        "EXPECTED_REFUSAL_REQUIRES_REVIEW",
    }
    return {
        "version": ANSWER_DELIVERY_VERSION,
        "answer_origin": origin,
        "delivery_status": delivery_status,
        "structural_pass": structural_pass,
        "semantic_acceptance": "MANUAL_REVIEW_REQUIRED",
    }


def _latest_finalization_outcome(
    events: Sequence[Mapping[str, Any]],
) -> str | None:
    outcomes = [
        str(event.get("outcome"))
        for event in events
        if event.get("event_type") == "answer_finalization_end"
        and event.get("outcome") is not None
    ]
    return outcomes[-1] if outcomes else None


def _answer_origin(
    answer: str,
    finalization_outcome: str | None,
    has_runtime_marker: bool,
) -> str:
    if not answer:
        return "NONE"
    if finalization_outcome is not None:
        if finalization_outcome == "succeeded":
            return "MODEL_SYNTHESIS"
        return "RUNTIME_STOP"
    if has_runtime_marker:
        return "RUNTIME_STOP"
    return "MODEL_FINAL"


def _looks_like_refusal(answer: str) -> bool:
    lowered = answer.casefold()
    return any(marker.casefold() in lowered for marker in _REFUSAL_MARKERS)
