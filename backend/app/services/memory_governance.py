"""M23: memory safety & governance primitives (pure logic).

记忆安全与用户可控治理的确定性规则（无 IO、无 LLM）：

- 记忆类型 / 状态 / 审核状态 / 敏感级别常量。
- MemoryWriteGate（M23 版）：按类型+信任+证据准入；秘密/凭据拒绝；
  相同内容去重；矛盾内容生成冲突而非静默覆盖。
- 状态机：correct / disable / restore / delete / review / expire。
- 检索可入集：仅 active（expired/disabled/deleted/pending_review 不入普通上下文）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256

from app.services.content_security import (
    HIGH_RISK_SCORE,
    TRUST_OPERATOR_INPUT,
    TRUST_REVIEWED_EVIDENCE,
    TRUST_SYSTEM_CONTROL,
)

# ---------- 记忆类型 ----------

MEMORY_TYPE_CONVERSATION_SUMMARY = "conversation_summary"
MEMORY_TYPE_CASE_FACT = "case_fact"
MEMORY_TYPE_CASE_HYPOTHESIS = "case_hypothesis"
MEMORY_TYPE_OPERATOR_PREFERENCE = "operator_preference"
MEMORY_TYPE_PROCEDURAL = "procedural"
MEMORY_TYPE_EXTERNAL_EXCERPT = "external_excerpt"

MEMORY_TYPES: frozenset[str] = frozenset(
    {
        MEMORY_TYPE_CONVERSATION_SUMMARY,
        MEMORY_TYPE_CASE_FACT,
        MEMORY_TYPE_CASE_HYPOTHESIS,
        MEMORY_TYPE_OPERATOR_PREFERENCE,
        MEMORY_TYPE_PROCEDURAL,
        MEMORY_TYPE_EXTERNAL_EXCERPT,
    }
)

#: 外部内容/模型推断/运行摘要等低信任类型（不可自我提升）。
NON_ESCALATING_TYPES: frozenset[str] = frozenset(
    {
        MEMORY_TYPE_EXTERNAL_EXCERPT,
        MEMORY_TYPE_CASE_HYPOTHESIS,
        MEMORY_TYPE_CONVERSATION_SUMMARY,
    }
)

# ---------- 状态与审核 ----------

MEMORY_STATUS_ACTIVE = "active"
MEMORY_STATUS_PENDING_REVIEW = "pending_review"
MEMORY_STATUS_SUPERSEDED = "superseded"
MEMORY_STATUS_EXPIRED = "expired"
MEMORY_STATUS_DISABLED = "disabled"
MEMORY_STATUS_DELETED = "deleted"

MEMORY_STATUSES: frozenset[str] = frozenset(
    {
        MEMORY_STATUS_ACTIVE,
        MEMORY_STATUS_PENDING_REVIEW,
        MEMORY_STATUS_SUPERSEDED,
        MEMORY_STATUS_EXPIRED,
        MEMORY_STATUS_DISABLED,
        MEMORY_STATUS_DELETED,
    }
)

REVIEW_UNREVIEWED = "unreviewed"
REVIEW_PENDING = "pending_review"
REVIEW_ACCEPTED = "accepted"
REVIEW_REJECTED = "rejected"
REVIEW_LEGACY = "legacy_unreviewed"

REVIEW_STATES: frozenset[str] = frozenset(
    {
        REVIEW_UNREVIEWED,
        REVIEW_PENDING,
        REVIEW_ACCEPTED,
        REVIEW_REJECTED,
        REVIEW_LEGACY,
    }
)

SENSITIVITY_LOW = "low"
SENSITIVITY_MEDIUM = "medium"
SENSITIVITY_HIGH = "high"

SENSITIVITIES: frozenset[str] = frozenset(
    {SENSITIVITY_LOW, SENSITIVITY_MEDIUM, SENSITIVITY_HIGH}
)

INDEX_PENDING = "pending"
INDEX_INDEXED = "indexed"
INDEX_REMOVED = "removed"

#: 可进入普通上下文的记忆状态（只读视角）。
RETRIEVABLE_STATUSES: frozenset[str] = frozenset({MEMORY_STATUS_ACTIVE})


# ---------- 类型策略 ----------

#: procedural 记忆只能来自受信版本化配置（system_control）。
PROCEDURAL_ALLOWED_TRUST: frozenset[str] = frozenset({TRUST_SYSTEM_CONTROL})
#: case_fact 必须带证据且信任等级足够。
CASE_FACT_ALLOWED_TRUST: frozenset[str] = frozenset(
    {TRUST_REVIEWED_EVIDENCE, TRUST_OPERATOR_INPUT, TRUST_SYSTEM_CONTROL}
)
#: operator_preference 只能来自用户明确表达。
PREFERENCE_ALLOWED_TRUST: frozenset[str] = frozenset({TRUST_OPERATOR_INPUT})


def memory_type_for_kind(kind: str) -> str:
    """既有 kind 到 M23 memory_type 的映射（写入路径默认）。"""
    mapping = {
        "fact": MEMORY_TYPE_CASE_FACT,
        "constraint": MEMORY_TYPE_OPERATOR_PREFERENCE,
        "preference": MEMORY_TYPE_OPERATOR_PREFERENCE,
        "correction": MEMORY_TYPE_CASE_FACT,
        "summary": MEMORY_TYPE_CONVERSATION_SUMMARY,
        "platform_profile": MEMORY_TYPE_CASE_HYPOTHESIS,
    }
    return mapping.get(kind, MEMORY_TYPE_EXTERNAL_EXCERPT)


def content_hash(content: str) -> str:
    return sha256(content.encode("utf-8", errors="replace")).hexdigest()


# ---------- 秘密 / 高敏感 PII 扫描（值级，防日志泄漏仅存摘要） ----------

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|password|passwd|pwd|token)\s*[=:]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~-]{12,}"),
    re.compile(r"(?i)sk-[A-Za-z0-9]{16,}"),
    re.compile(r"\b[0-9a-f]{32,64}\b"),
    re.compile(r"(?i)(authorization|private[_-]?key)\s*[=:]\s*\S+"),
)
_PII_PATTERNS = (
    re.compile(r"\b1[3-9]\d{9}\b"),  # 中国大陆手机号
    re.compile(r"\b\d{17}[\dXx]\b"),  # 身份证
    re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"),  # 邮箱
)


def scan_for_secrets(content: str) -> tuple[bool, list[str]]:
    """返回 (是否含秘密/凭据, 命中类别摘要)。只返回类别，不返回原文。"""
    hits: list[str] = []
    for index, pattern in enumerate(_SECRET_PATTERNS):
        if pattern.search(content):
            hits.append(f"secret_pattern_{index}")
    return bool(hits), hits


def scan_for_sensitive_pii(content: str) -> tuple[bool, list[str]]:
    """高敏感个人信息检测（手机号/身份证/邮箱）。"""
    hits: list[str] = []
    if _PII_PATTERNS[0].search(content):
        hits.append("phone_number")
    if _PII_PATTERNS[1].search(content):
        hits.append("id_card")
    if _PII_PATTERNS[2].search(content):
        hits.append("email")
    return bool(hits), hits


def sensitivity_of(content: str) -> str:
    """敏感级别：含秘密/凭据 -> high；含高敏感 PII -> medium；否则 low。"""
    has_secret, _ = scan_for_secrets(content)
    if has_secret:
        return SENSITIVITY_HIGH
    has_pii, _ = scan_for_sensitive_pii(content)
    if has_pii:
        return SENSITIVITY_MEDIUM
    return SENSITIVITY_LOW


# ---------- 写入 Gate ----------

@dataclass(frozen=True, slots=True)
class WriteDecision:
    """记忆写入判定：allow / deny / needs_review。"""

    decision: str
    reason: str
    review_state: str = REVIEW_UNREVIEWED

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def needs_review(self) -> bool:
        return self.decision == "needs_review"

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "review_state": self.review_state,
        }


class MemoryWriteGate:
    """M23 写入 Gate：按类型/信任/证据准入，秘密与越权一律拒绝。

    与 M16 的 MemoryWriteGate 互补：本 Gate 处理类型级策略（外部内容不
    能直接写事实/程序规则/偏好），M16 处理内容级风险信号。写入路径同时
    通过两道 Gate。
    """

    @classmethod
    def evaluate(
        cls,
        *,
        memory_type: str,
        trust_level: str,
        risk_score: float,
        has_evidence: bool = False,
        conflicting: bool = False,
        explicit_user_input: bool = False,
    ) -> WriteDecision:
        # 内容级高风险：无条件拒绝（不进入长期记忆）。
        if risk_score >= HIGH_RISK_SCORE:
            return WriteDecision("deny", "内容包含高风险注入/风险信号，拒绝写入")
        if memory_type not in MEMORY_TYPES:
            return WriteDecision("deny", f"未知记忆类型：{memory_type}")
        if memory_type == MEMORY_TYPE_PROCEDURAL:
            if trust_level not in PROCEDURAL_ALLOWED_TRUST:
                return WriteDecision(
                    "deny", "procedural 记忆只能来自受信版本化配置（system_control）"
                )
            return WriteDecision("allow", "受信配置写入程序规则")
        if memory_type == MEMORY_TYPE_OPERATOR_PREFERENCE:
            if trust_level not in PREFERENCE_ALLOWED_TRUST or not explicit_user_input:
                return WriteDecision(
                    "deny", "operator_preference 只能来自用户明确表达（operator_input）"
                )
            return WriteDecision("allow", "用户明确偏好")
        if memory_type == MEMORY_TYPE_CASE_FACT:
            if trust_level not in CASE_FACT_ALLOWED_TRUST:
                return WriteDecision(
                    "needs_review",
                    "事实候选缺少高信任来源，进入待审核",
                    REVIEW_PENDING,
                )
            if not has_evidence:
                return WriteDecision(
                    "needs_review",
                    "case_fact 必须带 Evidence 引用，进入待审核",
                    REVIEW_PENDING,
                )
            if conflicting:
                return WriteDecision(
                    "needs_review",
                    "与既有事实冲突，进入待审核而非静默覆盖",
                    REVIEW_PENDING,
                )
            return WriteDecision(
                "allow", "带证据的高信任事实", REVIEW_ACCEPTED
            )
        if memory_type == MEMORY_TYPE_CASE_HYPOTHESIS:
            return WriteDecision(
                "allow", "模型推断写入假设（不得自动转为事实）"
            )
        if memory_type == MEMORY_TYPE_CONVERSATION_SUMMARY:
            return WriteDecision(
                "allow", "运行摘要（摘要非事实标签由上下文层标注）"
            )
        if memory_type == MEMORY_TYPE_EXTERNAL_EXCERPT:
            return WriteDecision(
                "deny", "外部摘录默认留在知识/证据层，不升级为长期记忆"
            )
        return WriteDecision("deny", "未匹配的记忆类型策略")


# ---------- 冲突检测（纯规则） ----------

_CONFLICT_TOPIC_THRESHOLD = 0.3


def _topic_similarity(left: str, right: str) -> float:
    """字符 bigram Dice 系数（无分词依赖的主题相关度）。"""
    def bigrams(value: str) -> set[str]:
        chars = "".join(value.split())
        return {chars[i : i + 2] for i in range(len(chars) - 1)}

    left_grams = bigrams(left)
    right_grams = bigrams(right)
    if not left_grams or not right_grams:
        return 0.0
    overlap = len(left_grams & right_grams)
    return 2 * overlap / (len(left_grams) + len(right_grams))


def detect_conflict(
    new_content: str,
    existing: list[dict[str, object]],
    *,
    memory_type: str,
    threshold: float = _CONFLICT_TOPIC_THRESHOLD,
) -> list[dict[str, object]]:
    """找出与 new_content 主题相关且内容不同的既有事实类记忆（冲突候选）。

    existing 元素需含 content / id / memory_type / status。只对事实类
    记忆做冲突检测，且忽略已删除/已失效记录。
    """
    if memory_type not in {MEMORY_TYPE_CASE_FACT, MEMORY_TYPE_CASE_HYPOTHESIS}:
        return []
    conflicts: list[dict[str, object]] = []
    for item in existing:
        if item.get("status") not in {MEMORY_STATUS_ACTIVE, MEMORY_STATUS_PENDING_REVIEW}:
            continue
        if item.get("memory_type") not in {
            MEMORY_TYPE_CASE_FACT,
            MEMORY_TYPE_CASE_HYPOTHESIS,
        }:
            continue
        other_content = str(item.get("content") or "")
        if other_content == new_content:
            continue
        if _topic_similarity(new_content, other_content) >= threshold:
            conflicts.append(
                {
                    "memory_id": str(item.get("id") or ""),
                    "content": other_content,
                    "similarity": round(
                        _topic_similarity(new_content, other_content), 4
                    ),
                }
            )
    return conflicts


# ---------- 状态机 ----------

#: 允许的 (当前状态, 动作) 转移；动作失败返回 None。
_TRANSITIONS: dict[tuple[str, str], str] = {
    (MEMORY_STATUS_ACTIVE, "disable"): MEMORY_STATUS_DISABLED,
    (MEMORY_STATUS_PENDING_REVIEW, "disable"): MEMORY_STATUS_DISABLED,
    (MEMORY_STATUS_ACTIVE, "delete"): MEMORY_STATUS_DELETED,
    (MEMORY_STATUS_PENDING_REVIEW, "delete"): MEMORY_STATUS_DELETED,
    (MEMORY_STATUS_DISABLED, "delete"): MEMORY_STATUS_DELETED,
    (MEMORY_STATUS_EXPIRED, "delete"): MEMORY_STATUS_DELETED,
    (MEMORY_STATUS_SUPERSEDED, "delete"): MEMORY_STATUS_DELETED,
    (MEMORY_STATUS_DISABLED, "restore"): MEMORY_STATUS_ACTIVE,
    (MEMORY_STATUS_EXPIRED, "restore"): MEMORY_STATUS_ACTIVE,
    (MEMORY_STATUS_PENDING_REVIEW, "restore"): MEMORY_STATUS_PENDING_REVIEW,
    (MEMORY_STATUS_PENDING_REVIEW, "review_accept"): MEMORY_STATUS_ACTIVE,
    (MEMORY_STATUS_PENDING_REVIEW, "review_reject"): MEMORY_STATUS_DISABLED,
    (MEMORY_STATUS_ACTIVE, "review_reject"): MEMORY_STATUS_DISABLED,
    # 维护动作
    (MEMORY_STATUS_ACTIVE, "expire"): MEMORY_STATUS_EXPIRED,
    (MEMORY_STATUS_PENDING_REVIEW, "expire"): MEMORY_STATUS_EXPIRED,
    (MEMORY_STATUS_DISABLED, "expire"): MEMORY_STATUS_EXPIRED,
    (MEMORY_STATUS_ACTIVE, "correct"): MEMORY_STATUS_SUPERSEDED,
    (MEMORY_STATUS_PENDING_REVIEW, "correct"): MEMORY_STATUS_SUPERSEDED,
}


def status_transition(current: str, action: str) -> str | None:
    """返回转移后的状态；非法转移返回 None（由调用方 409）。"""
    return _TRANSITIONS.get((current, action))


def retrievable(memory: dict[str, object]) -> bool:
    """普通上下文检索可入集：仅 active；过期/停用/删除/待审核不入。"""
    return str(memory.get("status") or "") in RETRIEVABLE_STATUSES


# ---------- 摘要非事实标签 ----------

_SUMMARY_NOT_FACT_TAG = "摘要为模型生成的压缩叙述，不等同于事实，引用时需回查原文"


def summary_tag(memory_type: str) -> str:
    """conversation_summary 附带"摘要非事实"标签。"""
    if memory_type == MEMORY_TYPE_CONVERSATION_SUMMARY:
        return _SUMMARY_NOT_FACT_TAG
    return ""
