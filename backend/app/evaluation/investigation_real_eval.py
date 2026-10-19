"""Small helpers for the frozen investigation_real_eval_v1 harness."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Mapping, Sequence

from app.core.errors import ApplicationError

INVESTIGATION_REAL_SUITE = "investigation_real_eval_v1"
FAULT_TRANSIENT_READ = "fault:transient_read"
FAULT_PERSISTENT_READ = "fault:persistent_read"

# All exact, case-scoped DB01–DB09 reads. The persistent fault closes every
# exact-count/list/status alternative while leaving other tool semantics alone.
EXACT_DB_READ_TOOLS = frozenset(
    {
        "get_case_data_overview",
        "query_social_posts",
        "get_social_post",
        "query_social_comments",
        "aggregate_social_data",
        "query_findings",
        "query_review_items",
        "query_reports",
        "query_case_activity",
    }
)


class EvalToolRegistry:
    """Evaluation-only proxy that records returned read observations and faults.

    The wrapped registry remains the production registry. The proxy changes no
    ToolSpec, permission, route, or handler and is only constructed by the
    versioned investigation evaluation runner.
    """

    def __init__(
        self,
        registry: Any,
        *,
        task_tags: Sequence[str],
        observation_recorder: Any,
        enable_faults: bool,
    ) -> None:
        self._registry = registry
        self._tags = frozenset(task_tags)
        self._recorder = observation_recorder
        self._enable_faults = enable_faults
        self._transient_injected = False
        self._injected_failures: list[dict[str, str]] = []
        if self._tags & {FAULT_TRANSIENT_READ, FAULT_PERSISTENT_READ} == {
            FAULT_TRANSIENT_READ,
            FAULT_PERSISTENT_READ,
        }:
            raise ValueError("a task may declare only one controlled read fault")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._registry, name)

    async def invoke_with_meta(self, name: str, arguments: dict[str, object], **kwargs: Any) -> Any:
        try:
            code = self._fault_code(name)
            if code:
                error = ApplicationError(
                    f"Controlled evaluation read fault for {name}", code=code
                )
                self._injected_failures.append({"tool": name, "error_code": code})
                self._record(name, arguments, kwargs, status="failed", error=error)
                raise error

            invocation = await self._registry.invoke_with_meta(
                name, arguments, **kwargs
            )
        except Exception as exc:
            if not self._was_recorded_failure(name, kwargs):
                self._record(name, arguments, kwargs, status="failed", error=exc)
            raise

        if self._is_read_observation(name):
            self._recorder.record_tool_call(
                tool=name,
                tool_call_id=self._call_id(kwargs),
                arguments=arguments,
                status="succeeded",
                result=invocation.output,
                duration_ms=getattr(invocation, "duration_ms", None),
            )
        return invocation

    def fault_summary(self) -> dict[str, object]:
        return {
            "profiles": sorted(
                self._tags & {FAULT_TRANSIENT_READ, FAULT_PERSISTENT_READ}
            ),
            "injected_failures": list(self._injected_failures),
            "injected_failure_count": len(self._injected_failures),
        }

    def _fault_code(self, tool: str) -> str | None:
        if not self._enable_faults or tool not in EXACT_DB_READ_TOOLS:
            return None
        if FAULT_TRANSIENT_READ in self._tags:
            if tool == "query_social_posts" and not self._transient_injected:
                self._transient_injected = True
                return "eval_injected_transient_read"
            return None
        if FAULT_PERSISTENT_READ in self._tags:
            return "eval_injected_persistent_read"
        return None

    def _is_read_observation(self, tool: str) -> bool:
        try:
            spec = self._registry.get(tool)
        except Exception:  # noqa: BLE001 - unknown tool result is still a trace failure
            return False
        permissions = tuple(str(item).lower() for item in (spec.permissions or ()))
        return spec.side_effect == "none" and any("read" in item for item in permissions)

    @staticmethod
    def _call_id(kwargs: Mapping[str, Any]) -> str | None:
        security_context = kwargs.get("security_context") or {}
        call_id = (
            security_context.get("tool_call_id")
            if isinstance(security_context, Mapping)
            else None
        )
        return str(call_id) if call_id else None

    def _record(
        self,
        tool: str,
        arguments: Mapping[str, object],
        kwargs: Mapping[str, Any],
        *,
        status: str,
        error: Exception,
    ) -> None:
        if not self._is_read_observation(tool):
            return
        self._recorder.record_tool_call(
            tool=tool,
            tool_call_id=self._call_id(kwargs),
            arguments=arguments,
            status=status,
            error_code=str(getattr(error, "code", type(error).__name__)),
            error_message=str(error)[:300],
        )

    def _was_recorded_failure(self, tool: str, kwargs: Mapping[str, Any]) -> bool:
        call_id = self._call_id(kwargs)
        return bool(
            call_id
            and any(
                item.get("tool_call_id") == call_id
                and item.get("status") == "failed"
                for item in self._recorder.observations()
            )
        )


def controlled_fault_profile(task: Any) -> str | None:
    """Return the task's controlled-fault profile for this frozen suite only."""
    if getattr(task, "suite_version", "") != INVESTIGATION_REAL_SUITE:
        return None
    tags = set(getattr(task, "tags", ()))
    if FAULT_TRANSIENT_READ in tags:
        return FAULT_TRANSIENT_READ
    if FAULT_PERSISTENT_READ in tags:
        return FAULT_PERSISTENT_READ
    return None


def process_metrics(
    *,
    tool_calls: Sequence[Any],
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    """Count observed tool failures and repeated calls with no new observation."""
    failures = [call for call in tool_calls if call.status in {"failed", "error"}]
    by_tool = Counter(str(call.tool) for call in failures)
    by_code = Counter(str(call.error_code or "unknown") for call in failures)
    observation_by_call = {
        str(item.get("tool_call_id")): item
        for item in observations
        if item.get("tool_call_id")
    }
    previous: dict[tuple[str, str], tuple[str, str]] = {}
    repeats: list[dict[str, object]] = []
    for call in tool_calls:
        args_json = json.dumps(
            call.arguments or {}, sort_keys=True, ensure_ascii=False, default=str
        )
        signature = hashlib.sha256(args_json.encode("utf-8")).hexdigest()
        key = (str(call.tool), signature)
        observation = observation_by_call.get(str(call.id))
        fingerprint = ""
        if observation and observation.get("status") == "succeeded":
            result_json = json.dumps(
                observation.get("result"), sort_keys=True, ensure_ascii=False, default=str
            )
            fingerprint = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
        prior = previous.get(key)
        if prior is not None and (not fingerprint or fingerprint == prior[1]):
            repeats.append(
                {
                    "tool": call.tool,
                    "call_id": call.id,
                    "repeats_call_id": prior[0],
                    "reason": "same tool and normalized arguments produced no new observation",
                }
            )
        previous[key] = (str(call.id), fingerprint)
    return {
        "tool_failure_counts": {
            "total": len(failures),
            "by_tool": dict(sorted(by_tool.items())),
            "by_error_code": dict(sorted(by_code.items())),
        },
        "unproductive_repeat_count": len(repeats),
        "unproductive_repeats": repeats,
    }
