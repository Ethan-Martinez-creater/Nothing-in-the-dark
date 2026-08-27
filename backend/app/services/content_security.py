"""M16: untrusted-content defense - trust labels, detectors and guardrails.

Protects the harness from prompt-injection in social posts, pages, OCR/ASR,
tool output and MCP results.  The design treats detector signals as one layer
only: deterministic policy (ToolPolicy / Approval / Memory Gate) remains the
final authority even when every detector misses.

Pure, dependency-free logic (mirrors the ``services/evaluation.py`` style):
nothing here talks to a database or an LLM.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

TRUST_SYSTEM_CONTROL = "system_control"
TRUST_OPERATOR_INPUT = "operator_input"
TRUST_REVIEWED_EVIDENCE = "reviewed_evidence"
TRUST_EXTERNAL_CONTENT = "external_content"
TRUST_TOOL_DIAGNOSTIC = "tool_diagnostic"
TRUST_GENERATED_CONTENT = "generated_content"

TRUST_LEVELS: frozenset[str] = frozenset(
    {
        TRUST_SYSTEM_CONTROL,
        TRUST_OPERATOR_INPUT,
        TRUST_REVIEWED_EVIDENCE,
        TRUST_EXTERNAL_CONTENT,
        TRUST_TOOL_DIAGNOSTIC,
        TRUST_GENERATED_CONTENT,
    }
)

_IMMUTABLE_LOW_TRUST: frozenset[str] = frozenset(
    {TRUST_EXTERNAL_CONTENT, TRUST_TOOL_DIAGNOSTIC, TRUST_GENERATED_CONTENT}
)

DEFAULT_TRUST = TRUST_EXTERNAL_CONTENT

SEVERITY_SCORE = {"critical": 1.0, "high": 0.8, "medium": 0.5, "low": 0.2}

DISPOSITION_ALLOWED = "allowed"
DISPOSITION_ISOLATED = "isolated"
DISPOSITION_TRUNCATED = "truncated"
DISPOSITION_QUARANTINED = "quarantined"
DISPOSITION_BLOCKED = "blocked"

DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_ISOLATE = "isolate"
DECISION_TRUNCATE = "truncate"
DECISION_REQUIRE_APPROVAL = "require_approval"

HIGH_RISK_SCORE = 0.7
MEDIUM_RISK_SCORE = 0.4


@dataclass(frozen=True, slots=True)
class RiskSignal:
    """One detector hit: name, severity, short evidence and a stable id."""

    name: str
    severity: str
    evidence: str
    score: float = 0.0

    @classmethod
    def build(
        cls,
        name: str,
        severity: str,
        evidence: str,
    ) -> RiskSignal:
        return cls(
            name=name,
            severity=severity,
            evidence=evidence[:200],
            score=SEVERITY_SCORE.get(severity, 0.0),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "severity": self.severity,
            "evidence": self.evidence,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class ContentAssessment:
    """Assessment of one piece of content: score + signals + disposition."""

    object_type: str
    object_id: str
    trust_level: str
    score: float
    signals: tuple[RiskSignal, ...]
    disposition: str = DISPOSITION_ALLOWED
    detector: str = "content_security_detectors"
    detector_version: str = "1.0"
    reason: str = ""
    content_hash: str = ""
    reviewed: bool = False

    @property
    def highest_severity(self) -> str | None:
        if not self.signals:
            return None
        order = ["critical", "high", "medium", "low"]
        for level in order:
            if any(s.severity == level for s in self.signals):
                return level
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "object_type": self.object_type,
            "object_id": self.object_id,
            "trust_level": self.trust_level,
            "score": self.score,
            "signals": [s.to_dict() for s in self.signals],
            "disposition": self.disposition,
            "detector": self.detector,
            "detector_version": self.detector_version,
            "reason": self.reason,
            "content_hash": self.content_hash,
            "reviewed": self.reviewed,
        }


@dataclass(slots=True)
class ContentEnvelope:
    """Structured wrapper for any untrusted content entering the harness.

    ``content`` keeps the raw text; ``source`` identifies where it came from;
    ``trust`` is the immutable trust level; ``risk_signals`` are appended by
    the detectors; ``transformations`` records every mutation (truncation,
    isolation, redaction) so the chain stays auditable.
    """

    content: str
    source_type: str = ""
    source_id: str = ""
    trust: str = DEFAULT_TRUST
    classification: str = "general"
    acquired_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    review_state: str = "unreviewed"
    risk_signals: list[RiskSignal] = field(default_factory=list)
    transformations: list[str] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        return sha256(self.content.encode("utf-8", errors="replace")).hexdigest()

    def with_trust(self, trust: str) -> ContentEnvelope:
        """Return a copy with a new trust level (never auto-promote low)."""
        if trust not in TRUST_LEVELS:
            raise ValueError("Unknown trust level: " + trust)
        return ContentEnvelope(
            content=self.content,
            source_type=self.source_type,
            source_id=self.source_id,
            trust=trust,
            classification=self.classification,
            acquired_at=self.acquired_at,
            review_state=self.review_state,
            risk_signals=list(self.risk_signals),
            transformations=list(self.transformations),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "content": self.content,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "trust": self.trust,
            "classification": self.classification,
            "acquired_at": self.acquired_at.isoformat(),
            "review_state": self.review_state,
            "risk_signals": [s.to_dict() for s in self.risk_signals],
            "transformations": list(self.transformations),
            "content_hash": self.content_hash,
        }


class InstructionOverrideDetector:
    """Directive hijacking: fake system roles, ignore-instructions, nesting."""

    name = "instruction_override"
    version = "1.0"

    _IGNORE_PATTERNS = (
        "忽略之前", "忽略以上", "忽略此前", "无视之前", "无视以上",
        "不需要理会之前的", "ignore all previous", "ignore previous instructions",
        "ignore prior instructions", "ignore everything above",
        "disregard the previous", "forget everything", "forget all previous",
        "override your instructions", "you are no longer", "from now on you are",
    )
    _FAKE_ROLE_PATTERNS = (
        "<system", "<|system|>", "system:", "system role", "developer role",
        "你现在的身份是系统", "你现在是系统", "假装你是系统", "以系统提示的身份",
        "act as the system", "pretend to be the system", "you are the system prompt",
    )
    _NESTED_DIRECTIVE_PATTERNS = (
        "<instruction>", "<instructions>", "<command>", "```system",
        "```developer", "```instruction", "以下指令优先级最高", "以上内容无效",
        "ignore the text above",
    )

    def detect(self, text: str) -> list[RiskSignal]:
        lowered = text.lower()
        signals: list[RiskSignal] = []
        for pattern in self._IGNORE_PATTERNS:
            if pattern.lower() in lowered:
                signals.append(RiskSignal.build(self.name, "high", "指令覆盖关键词: " + pattern))
                break
        for pattern in self._FAKE_ROLE_PATTERNS:
            if pattern.lower() in lowered:
                signals.append(
                    RiskSignal.build(
                        self.name, "critical", "伪造系统角色标记: " + pattern
                    )
                )
                break
        for pattern in self._NESTED_DIRECTIVE_PATTERNS:
            if pattern.lower() in lowered:
                signals.append(RiskSignal.build(self.name, "medium", "嵌套指令结构: " + pattern))
                break
        return signals


class SecretRequestDetector:
    """Requests to exfiltrate secrets, prompts, credentials or other cases."""

    name = "secret_request"
    version = "1.0"

    _SECRET_FILE_PATTERNS = (
        ".env", "cookie.txt", "cookies.txt", "token.txt", "api.key", "apikey",
        "secret_key", "credentials.json", "配置密钥", "登录凭证", "cookie 内容",
    )
    _SECRET_ASK_PATTERNS = (
        "读取 .env", "读取env", "输出你的系统提示", "你的系统提示词",
        "系统提示词原文", "打印你的 instructions", "告诉我你的 instructions",
        "显示系统 prompt", "read your system prompt", "output your system prompt",
        "reveal your instructions", "print your instructions",
        "give me your system prompt", "其他案件的", "其他案件的内容", "另一个案件",
        "cookie 值", "authorization header",
    )
    _SECRET_KEYS = (
        "api_key", "apikey", "access_token", "refresh_token", "authorization",
        "secret", "password", "client_secret",
    )

    def detect(self, text: str) -> list[RiskSignal]:
        lowered = text.lower()
        signals: list[RiskSignal] = []
        for pattern in self._SECRET_FILE_PATTERNS:
            if pattern.lower() in lowered:
                signals.append(RiskSignal.build(self.name, "high", "请求访问秘密文件: " + pattern))
                break
        for pattern in self._SECRET_ASK_PATTERNS:
            if pattern.lower() in lowered:
                signals.append(
                    RiskSignal.build(
                        self.name, "critical", "索取秘密/提示词: " + pattern
                    )
                )
                break
        for key in self._SECRET_KEYS:
            if key in lowered:
                signals.append(RiskSignal.build(self.name, "medium", "提及敏感字段: " + key))
                break
        return signals


class ToolInductionDetector:
    """Forces another tool call, scope expansion, or policy bypass."""

    name = "tool_induction"
    version = "1.0"

    _TOOL_CALL_PATTERNS = (
        "必须调用", "请调用工具", "现在调用", "调用另一个工具", "直接调用",
        "不要问我", "不要审批", "无需审批", "跳过审批", "绕过审批", "解除审批",
        "自动批准", "扩大平台范围", "扩大时间范围", "增加预算", "解除预算",
        "解除限制", "解锁权限", "以管理员身份", "you must call", "call the tool",
        "invoke the tool", "skip approval", "bypass approval", "disable approval",
        "no approval needed", "expand your permissions", "grant yourself",
        "unlock access", "ignore the approval", "do not ask for permission",
    )
    _PERSIST_PATTERNS = (
        "写入长期记忆", "保存为约束", "记住这条", "写进记忆", "永久记住",
        "写入数据库", "write to memory", "remember this", "save as constraint",
        "persist this", "store this permanently",
    )

    def detect(self, text: str) -> list[RiskSignal]:
        lowered = text.lower()
        signals: list[RiskSignal] = []
        for pattern in self._TOOL_CALL_PATTERNS:
            if pattern.lower() in lowered:
                severity = "high" if ("审批" in pattern or "approval" in pattern) else "medium"
                signals.append(RiskSignal.build(self.name, severity, "工具诱导: " + pattern))
                break
        for pattern in self._PERSIST_PATTERNS:
            if pattern.lower() in lowered:
                signals.append(RiskSignal.build(self.name, "medium", "持久化诱导: " + pattern))
                break
        return signals


_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f]")
_BASE64_RE = re.compile(r"(?:[A-Za-z0-9+/]{4}){8,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")
_URL_ESCAPE_RE = re.compile(r"%(?:[0-9A-Fa-f]{2}){3,}")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


class EncodingEscapeDetector:
    """Hidden Unicode, base64/URL escaping, HTML comment smuggling."""

    name = "encoding_escape"
    version = "1.0"

    def detect(self, text: str) -> list[RiskSignal]:
        signals: list[RiskSignal] = []
        zero_width = _ZERO_WIDTH_RE.findall(text)
        if zero_width:
            signals.append(
                RiskSignal.build(
                    self.name, "medium", "隐藏零宽字符 x" + str(len(zero_width))
                )
            )
        base64_hits = _BASE64_RE.findall(text)
        if base64_hits:
            for hit in base64_hits[:3]:
                decoded: bytes | None = None
                try:
                    decoded = base64.b64decode(hit)
                except (binascii.Error, ValueError):
                    decoded = None
                if decoded and b"http" in decoded[:200].lower():
                    signals.append(RiskSignal.build(self.name, "high", "Base64 编码的链接载荷"))
                    break
        url_escaped = _URL_ESCAPE_RE.findall(text)
        if url_escaped:
            signals.append(
                RiskSignal.build(
                    self.name, "medium", "URL 编码载荷 x" + str(len(url_escaped))
                )
            )
        html_comments = _HTML_COMMENT_RE.findall(text)
        if html_comments:
            signals.append(
                RiskSignal.build(
                    self.name, "medium", "HTML 注释隐藏内容 x" + str(len(html_comments))
                )
            )
        return signals


def all_detectors() -> tuple[Any, ...]:
    return (
        InstructionOverrideDetector(),
        SecretRequestDetector(),
        ToolInductionDetector(),
        EncodingEscapeDetector(),
    )


HIGH_TOOL_INPUT_DECISION = DECISION_DENY


def combine_signals(signals: list[RiskSignal]) -> float:
    """Risk score = max severity; ties broken by count (saturating)."""
    if not signals:
        return 0.0
    top = max(signal.score for signal in signals)
    if top < 0.8:
        return round(top, 4)
    return round(min(1.0, top + 0.05 * (len(signals) - 1)), 4)


GuardrailRecorder = Callable[[dict[str, Any]], Any]


class ContentSecurityService:
    """Deterministic content-security layer.

    ``mode``: enforce (fail closed) or audit_only (record but allow).
    ``recorder`` persists decisions; failures there never break the tool path.
    """

    POLICY_VERSION = "1.0"

    def __init__(
        self,
        *,
        mode: str = "enforce",
        policy_version: str = POLICY_VERSION,
        recorder: GuardrailRecorder | None = None,
    ) -> None:
        if mode not in {"enforce", "audit_only"}:
            raise ValueError("Unknown content-security mode: " + mode)
        self.mode = mode
        self.policy_version = policy_version
        self._recorder = recorder
        self._detectors = all_detectors()

    def assess(
        self,
        envelope: ContentEnvelope,
        *,
        object_type: str = "content",
        object_id: str = "",
    ) -> ContentAssessment:
        signals: list[RiskSignal] = []
        for detector in self._detectors:
            try:
                signals.extend(detector.detect(envelope.content))
            except Exception:  # noqa: BLE001
                continue
        score = combine_signals(signals)
        disposition = DISPOSITION_ALLOWED
        reason = ""
        if score >= HIGH_RISK_SCORE and self.mode == "enforce":
            disposition = DISPOSITION_ISOLATED
            reason = "高风险内容已隔离：保留证据但不再原文注入上下文"
        elif score >= MEDIUM_RISK_SCORE and self.mode == "enforce":
            disposition = DISPOSITION_TRUNCATED
            reason = "中风险内容截断：仅保留摘要窗口"
        return ContentAssessment(
            object_type=object_type,
            object_id=object_id or envelope.source_id,
            trust_level=envelope.trust,
            score=score,
            signals=tuple(signals),
            disposition=disposition,
            reason=reason,
            content_hash=envelope.content_hash,
            reviewed=envelope.review_state == "accepted",
        )

    async def check_tool_input(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        run_id: str | None = None,
        turn_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        """Pre-execution guardrail over model-produced tool arguments."""
        summary = _text_from_arguments(arguments)
        envelope = ContentEnvelope(
            content=summary,
            source_type="tool_input",
            source_id=tool_name + ":" + (tool_call_id or ""),
            trust=TRUST_GENERATED_CONTENT,
            classification="tool_arguments",
        )
        assessment = self.assess(envelope, object_type="tool_input", object_id=tool_name)
        decision = DECISION_ALLOW
        reason = assessment.reason or "tool input allowed"
        if assessment.score >= HIGH_RISK_SCORE:
            if self.mode == "enforce":
                decision = HIGH_TOOL_INPUT_DECISION
                reason = "工具参数包含高风险注入信号，已阻断执行"
            else:
                reason = "[audit_only] 检测到高风险注入信号（未阻断）"
        elif assessment.score >= MEDIUM_RISK_SCORE:
            if self.mode == "enforce":
                decision = DECISION_REQUIRE_APPROVAL
                reason = "工具参数包含中风险信号，需审批后执行"
            else:
                reason = "[audit_only] 检测到中风险信号（未阻断）"
        return await self._decision(
            stage="tool_input", decision=decision, reason=reason,
            run_id=run_id, turn_id=turn_id, tool_call_id=tool_call_id,
            tool=tool_name, assessment=assessment,
        )

    async def check_tool_output(
        self,
        tool_name: str,
        output: dict[str, Any],
        *,
        run_id: str | None = None,
        turn_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Post-execution guardrail: sanitize + scan the tool result.
        Returns ``(decision, sanitized_output)``; sanitization is lossy on
        purpose so the model never receives raw credentials from tool output.
        """
        sanitized, issues = ToolOutputSanitizer.sanitize(output)
        risk_text = _text_from_arguments(sanitized)
        envelope = ContentEnvelope(
            content=risk_text, source_type="tool_output", source_id=tool_name,
            trust=TRUST_TOOL_DIAGNOSTIC, classification="tool_output",
        )
        assessment = self.assess(envelope, object_type="tool_output", object_id=tool_name)
        decision = DECISION_ALLOW
        reason = "tool output sanitized"
        if issues.get("secret_like_values"):
            decision = DECISION_TRUNCATE
            reason = "工具输出疑似包含秘密值，已脱敏并截断"
        elif self.mode == "enforce" and assessment.score >= HIGH_RISK_SCORE:
            decision = DECISION_ISOLATE
            reason = "工具输出包含高风险注入信号，已隔离"
            sanitized = {
                "isolated": True,
                "tool": tool_name,
                "content_hash": assessment.content_hash,
                "reason": reason,
            }
        return (
            await self._decision(
                stage="tool_output", decision=decision, reason=reason,
                run_id=run_id, turn_id=turn_id, tool_call_id=tool_call_id,
                tool=tool_name, assessment=assessment,
            ),
            sanitized,
        )

    async def check_memory_write(
        self,
        content: str,
        *,
        source_type: str,
        source_id: str,
        trust: str,
        review_state: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """M16.7 memory-write gate: unreviewed low-trust content is denied."""
        envelope = ContentEnvelope(
            content=content, source_type=source_type, source_id=source_id,
            trust=trust, review_state=review_state, classification="memory",
        )
        assessment = self.assess(envelope, object_type="memory", object_id=source_id)
        allowed = MemoryWriteGate.allows(
            trust=trust, review_state=review_state, score=assessment.score,
        )
        decision = DECISION_ALLOW if allowed else DECISION_DENY
        reason = ("memory write allowed" if allowed
                  else "未审核外部内容禁止写入长期记忆（MemoryWriteGate）")
        return await self._decision(
            stage="memory_write", decision=decision, reason=reason,
            run_id=run_id, turn_id=None, tool_call_id=None,
            tool=tool_of_source(source_type), assessment=assessment,
        )

    async def context_policy(
        self,
        envelope: ContentEnvelope,
        *,
        object_type: str,
        object_id: str,
    ) -> tuple[str, ContentAssessment]:
        """Decide how external content enters the context window.
        High-risk content stays an isolated reference (hash + source),
        never verbatim text; the evidence remains available for review.
        """
        assessment = self.assess(envelope, object_type=object_type, object_id=object_id)
        if assessment.disposition == DISPOSITION_ISOLATED:
            return (
                ContextPolicy.isolation_reference(
                    object_id=assessment.object_id,
                    source_type=envelope.source_type,
                    trust=envelope.trust,
                    content_hash=assessment.content_hash,
                    severity=assessment.highest_severity,
                ),
                assessment,
            )
        if assessment.disposition == DISPOSITION_TRUNCATED:
            window = ContextPolicy.truncate_window(envelope.content)
            return (
                "[截断内容 " + assessment.object_id + "] 来源="
                + envelope.source_type + " 信任=" + envelope.trust
                + " 摘要=" + window,
                assessment,
            )
        return envelope.content, assessment

    async def _decision(
        self,
        *,
        stage: str,
        decision: str,
        reason: str,
        run_id: str | None,
        turn_id: str | None,
        tool_call_id: str | None,
        tool: str | None,
        assessment: ContentAssessment,
    ) -> dict[str, Any]:
        if (
            self.mode == "audit_only"
            and decision in {DECISION_DENY, DECISION_ISOLATE, DECISION_TRUNCATE}
        ):
            decision = DECISION_ALLOW
            reason = "[audit_only] " + reason
        record: dict[str, Any] = {
            "stage": stage,
            "decision": decision,
            "reason": reason,
            "policy_version": self.policy_version,
            "run_id": run_id,
            "turn_id": turn_id,
            "tool_call_id": tool_call_id,
            "tool": tool,
            "signal_ids": [s.name for s in assessment.signals],
            "content_hash": assessment.content_hash,
            "summary": _summarize_reason(reason, assessment),
            "assessment": assessment.to_dict(),
        }
        if self._recorder is not None:
            try:
                await self._recorder(record)
            except Exception:  # noqa: BLE001
                pass
        return record


class ContextPolicy:
    """High-risk isolation helpers (M16.4)."""

    TRUNCATE_WINDOW = 120

    @classmethod
    def truncate_window(cls, text: str) -> str:
        if len(text) <= cls.TRUNCATE_WINDOW:
            return text
        return text[: cls.TRUNCATE_WINDOW] + "…"

    @classmethod
    def isolation_reference(
        cls,
        *,
        object_id: str,
        source_type: str,
        trust: str,
        content_hash: str,
        severity: str | None,
    ) -> str:
        return (
            "[隔离内容 " + object_id + "] 来源=" + source_type + " 信任=" + trust
            + " 哈希=" + content_hash[:12] + " 风险=" + (severity or "none")
            + "（已保留证据，可人工审核）"
        )


class ToolOutputSanitizer:
    """M16.5 output guardrail: length / secret pattern / link scanning."""

    MAX_OUTPUT_CHARS = 20_000
    MAX_LIST_ITEMS = 500

    _SECRET_VALUE_RE = re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|token|authorization|bearer\s+[A-Za-z0-9._~-]{12,}|"
        r"secret[_-]?key|password|client[_-]?secret)\s*[:=]\s*[A-Za-z0-9._~+/=-]{12,}"
    )
    _URL_RE = re.compile(r"https?://[^\s]+")
    _SECRET_KEYS = frozenset(
        {
            "cookie",
            "cookies",
            "api_key",
            "apikey",
            "access_token",
            "refresh_token",
            "authorization",
            "secret",
            "password",
            "client_secret",
        }
    )

    @classmethod
    def sanitize(
        cls, value: object, *, depth: int = 0
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Deep-copy ``value`` with secrets redacted, oversized fields trimmed."""
        issues: dict[str, Any] = {
            "secret_like_values": 0,
            "truncated_fields": [],
            "url_count": 0,
        }
        sanitized = cls._walk(value, depth=depth, issues=issues)
        return sanitized, issues

    @classmethod
    def _walk(cls, value: object, *, depth: int, issues: dict[str, Any]) -> object:
        if depth > 6:
            issues["truncated_fields"].append("depth_limit")
            return "[深度超限]"
        if isinstance(value, str):
            if cls._SECRET_VALUE_RE.search(value):
                issues["secret_like_values"] += 1
                return "***"
            issues["url_count"] += len(cls._URL_RE.findall(value))
            if len(value) > cls.MAX_OUTPUT_CHARS:
                issues["truncated_fields"].append("string_length")
                return value[: cls.MAX_OUTPUT_CHARS] + "…"
            return value
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if str(key).lower() in cls._SECRET_KEYS:
                    issues["secret_like_values"] += 1
                    result[str(key)] = "***"
                    continue
                result[str(key)] = cls._walk(item, depth=depth + 1, issues=issues)
            return result
        if isinstance(value, list):
            items = value
            if len(items) > cls.MAX_LIST_ITEMS:
                issues["truncated_fields"].append("list_length")
                items = items[: cls.MAX_LIST_ITEMS]
            return [cls._walk(item, depth=depth + 1, issues=issues) for item in items]
        return value


class MemoryWriteGate:
    """M16.7: unreviewed low-trust content never becomes long-term memory."""

    @classmethod
    def allows(
        cls,
        *,
        trust: str,
        review_state: str,
        score: float,
    ) -> bool:
        if score >= HIGH_RISK_SCORE:
            return False
        if trust in _IMMUTABLE_LOW_TRUST:
            return review_state == "accepted"
        return True

    @classmethod
    def requires_review(cls, *, trust: str, score: float) -> bool:
        return score >= MEDIUM_RISK_SCORE or trust in _IMMUTABLE_LOW_TRUST


def _text_from_arguments(arguments: dict[str, Any]) -> str:
    """Flatten arguments to a stable text for detector scanning."""
    parts: list[str] = []
    for key, value in arguments.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            parts.append(key + "=" + str(value))
        else:
            try:
                parts.append(key + "=" + json.dumps(value, ensure_ascii=False, default=str))
            except Exception:  # noqa: BLE001
                parts.append(key + "=<unserializable>")
    return " ".join(parts)


def _summarize_reason(reason: str, assessment: ContentAssessment) -> str:
    signals = ", ".join(s.name for s in assessment.signals[:5])
    if signals:
        return reason + " [" + signals + "]"
    return reason


def tool_of_source(source_type: str) -> str | None:
    return source_type if source_type in {"tool_output", "tool_input"} else None


def normalize_trust_level(value: str | None) -> str:
    """Validate/coerce a trust-level string; unknown values fall back to
    ``external_content`` so low trust is the default."""
    if value in TRUST_LEVELS:
        return value
    return DEFAULT_TRUST

