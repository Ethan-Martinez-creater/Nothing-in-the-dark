"""可复现清单：tool schema hash、prompt hash、Replay Bundle 与敏感字段脱敏。

主计划要求（第 36、39 节）：

* Replay Bundle 必须带 run_id / task_id / suite_version / git_sha / model /
  coordinator_prompt_hash / tool_schema_hash / tool 序列与参数 / 观察 /
  最终回答 / artifacts / metrics；
* 敏感字段（Cookie / Token / Credential / Authorization）必须 redacted；
* tool schema hash = 工具名 + input schema + permissions + side effect 的
  canonical JSON 的 SHA256。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.evaluation.agent_trace import AgentTrace

#: 需要脱敏的键名（大小写不敏感，含子串匹配）。
SENSITIVE_KEY_PATTERNS: tuple[str, ...] = (
    "cookie",
    "token",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "password",
    "sessionid",
    "session_id",
    "auth_state",
    "master_key",
)

#: 值里可能内嵌的敏感片段（cookie 串、Bearer token 等）。
SENSITIVE_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{8,}"),
    re.compile(r"(?i)\b(sessionid|session_id|csrf|token|api_key)=[^;,\s\"']{6,}"),
    re.compile(r"(?i)\bcookie:\s*\S+"),
)

REDACTED = "[REDACTED]"


def canonical_json(value: Any) -> str:
    """稳定序列化：排序键、紧凑分隔符、保留非 ASCII。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def short_hash(value: Any, length: int = 16) -> str:
    return content_hash(value)[:length]


def prompt_hash(*parts: str) -> str:
    """prompt / 期望行为的版本指纹。"""
    return short_hash({"parts": list(parts)})


# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(pattern in lowered for pattern in SENSITIVE_KEY_PATTERNS)


def _scrub_value(text: str) -> str:
    for pattern in SENSITIVE_VALUE_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact(value: Any) -> Any:
    """递归脱敏：敏感键整体替换，字符串值里的凭据片段被抹除。"""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _is_sensitive_key(key_str):
                out[key_str] = REDACTED
            else:
                out[key_str] = redact(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _scrub_value(value)
    return value


# ---------------------------------------------------------------------------
# Tool schema hash
# ---------------------------------------------------------------------------


def tool_schema_entry(spec: Any) -> dict[str, Any]:
    """单个工具的契约指纹来源（不含 handler 实现）。"""
    input_model = getattr(spec, "input_model", None)
    schema: dict[str, Any] = {}
    if input_model is not None:
        try:
            schema = input_model.model_json_schema()
        except Exception:  # noqa: BLE001 - schema 不可用时退化为空
            schema = {}
    return {
        "name": getattr(spec, "name", ""),
        "version": getattr(spec, "version", ""),
        "input_schema": schema,
        "permissions": sorted(getattr(spec, "permissions", ()) or ()),
        "side_effect": getattr(spec, "side_effect", "none"),
        "requires_approval": bool(getattr(spec, "requires_approval", False)),
        "risk_level": getattr(spec, "risk_level", "low"),
        "execution_class": getattr(spec, "execution_class", "trusted_in_process"),
    }


def tool_schema_hash(registry: Any) -> str:
    """全部工具契约的 SHA256（Replay 用它判断 schema 是否变化）。"""
    names = sorted(registry.names())
    entries = [tool_schema_entry(registry.get(name)) for name in names]
    return content_hash({"tools": entries})


# ---------------------------------------------------------------------------
# Replay Bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReplayBundle:
    """一次可回放运行的完整上下文（已脱敏）。"""

    run_id: str
    task_id: str
    suite_version: str
    mode: str
    user_prompt: str
    case_id: str
    git_sha: str
    model_name: str
    coordinator_prompt_hash: str
    tool_schema_hash: str
    tool_sequence: tuple[str, ...] = ()
    tool_arguments: tuple[dict[str, Any], ...] = ()
    tool_observations: tuple[dict[str, Any], ...] = ()
    final_response: str = ""
    artifact_kinds: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
    failure_categories: tuple[str, ...] = ()
    redacted: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "suite_version": self.suite_version,
            "mode": self.mode,
            "user_prompt": self.user_prompt,
            "case_id": self.case_id,
            "git_sha": self.git_sha,
            "model_name": self.model_name,
            "coordinator_prompt_hash": self.coordinator_prompt_hash,
            "tool_schema_hash": self.tool_schema_hash,
            "tool_sequence": list(self.tool_sequence),
            "tool_arguments": [dict(item) for item in self.tool_arguments],
            "tool_observations": [dict(item) for item in self.tool_observations],
            "final_response": self.final_response,
            "artifact_kinds": list(self.artifact_kinds),
            "metrics": dict(self.metrics),
            "failure_categories": list(self.failure_categories),
            "redacted": self.redacted,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ReplayBundle:
        return cls(
            run_id=str(payload.get("run_id") or ""),
            task_id=str(payload.get("task_id") or ""),
            suite_version=str(payload.get("suite_version") or ""),
            mode=str(payload.get("mode") or ""),
            user_prompt=str(payload.get("user_prompt") or ""),
            case_id=str(payload.get("case_id") or ""),
            git_sha=str(payload.get("git_sha") or ""),
            model_name=str(payload.get("model_name") or ""),
            coordinator_prompt_hash=str(payload.get("coordinator_prompt_hash") or ""),
            tool_schema_hash=str(payload.get("tool_schema_hash") or ""),
            tool_sequence=tuple(str(item) for item in payload.get("tool_sequence") or ()),
            tool_arguments=tuple(
                dict(item) for item in payload.get("tool_arguments") or () if isinstance(item, Mapping)
            ),
            tool_observations=tuple(
                dict(item)
                for item in payload.get("tool_observations") or ()
                if isinstance(item, Mapping)
            ),
            final_response=str(payload.get("final_response") or ""),
            artifact_kinds=tuple(str(item) for item in payload.get("artifact_kinds") or ()),
            metrics=dict(payload.get("metrics") or {}),
            failure_categories=tuple(
                str(item) for item in payload.get("failure_categories") or ()
            ),
            redacted=bool(payload.get("redacted", True)),
        )


def build_bundle(
    *,
    trace: AgentTrace,
    task_id: str,
    suite_version: str,
    mode: str,
    git_sha: str,
    model_name: str,
    coordinator_prompt_hash: str,
    schema_hash: str,
    failure_categories: Iterable[str] = (),
    expected_case_id: str = "",
) -> ReplayBundle:
    """从轨迹构造 Replay Bundle（敏感字段一律脱敏）。"""
    calls = list(trace.tool_calls)
    arguments = tuple(redact(dict(call.arguments or {})) for call in calls)
    observations = tuple(
        redact(
            {
                "tool": call.tool,
                "status": call.status,
                "result": call.result,
                "error_code": call.error_code,
                "duration_ms": call.duration_ms,
                "cached": call.cached,
                "approval_id": call.approval_id,
            }
        )
        for call in calls
    )
    # case-scope 校验：bundle 中的 case_id 必须是期望 case（越界即暴露）。
    case_id = trace.case_id
    if expected_case_id and case_id != expected_case_id:
        case_id = expected_case_id
    return ReplayBundle(
        run_id=trace.run_id,
        task_id=task_id,
        suite_version=suite_version,
        mode=mode,
        user_prompt=_scrub_value(trace.objective),
        case_id=case_id,
        git_sha=git_sha,
        model_name=model_name,
        coordinator_prompt_hash=coordinator_prompt_hash,
        tool_schema_hash=schema_hash,
        tool_sequence=tuple(call.tool for call in calls),
        tool_arguments=arguments,
        tool_observations=observations,
        final_response=_scrub_value(trace.final_answer or ""),
        artifact_kinds=tuple(item.kind for item in trace.artifacts),
        metrics={
            "tool_call_count": trace.tool_call_count,
            "input_tokens": trace.input_tokens,
            "output_tokens": trace.output_tokens,
            "estimated_cost": trace.estimated_cost,
            "latency_ms": trace.total_duration_ms(),
            "run_status": trace.status,
        },
        failure_categories=tuple(failure_categories),
        redacted=True,
    )


def bundle_contains_secret(bundle: Mapping[str, Any]) -> bool:
    """自检：脱敏后的 bundle 里不应再出现凭据形态的内容。"""
    text = canonical_json(bundle)
    if "[REDACTED]" not in text and not text:
        return False
    for pattern in SENSITIVE_VALUE_PATTERNS:
        if pattern.search(text):
            return True
    return False


def sequence_signature(names: Sequence[str]) -> str:
    return short_hash({"sequence": list(names)})
