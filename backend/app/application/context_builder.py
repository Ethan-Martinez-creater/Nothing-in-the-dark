"""Per-run context assembly: constraints first, then memories, artifacts,
summary and a budgeted history window."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.llm import LLMMessage
from app.services.content_security import (
    TRUST_EXTERNAL_CONTENT,
    TRUST_GENERATED_CONTENT,
    TRUST_OPERATOR_INPUT,
    TRUST_REVIEWED_EVIDENCE,
    ContentEnvelope,
    ContentSecurityService,
)
from app.services.memory_governance import (
    MEMORY_STATUS_ACTIVE,
    MEMORY_TYPE_CASE_HYPOTHESIS,
    MEMORY_TYPE_CONVERSATION_SUMMARY,
    summary_tag,
)

# M16: 外部内容来源（帖子/评论/外部文档）默认 low-trust。
_EXTERNAL_SOURCE_TYPES = frozenset({"social_post", "social_comment", "document_chunk"})


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _truncate_to_budget(text: str, budget_tokens: int) -> str:
    if _estimate_tokens(text) <= budget_tokens:
        return text
    return text[: budget_tokens * 4]


@dataclass(slots=True)
class BuiltContext:
    system_context: str
    history_window: list[LLMMessage]
    stats: dict[str, Any] = field(default_factory=dict)


class ContextBuilder:
    """Assemble the system context and history window for one agent run.

    Priority (never-truncated first): case header + user constraints, then
    high-importance memories (importance >= 0.7), the artifact index, the
    latest conversation summary, and finally the recent history window.
    The token budget truncates lower-priority sections; constraints always
    survive. Any lookup failure degrades to the plain case header with the
    full history (equivalent to the pre-builder behaviour).
    """

    def __init__(
        self,
        repository: ApplicationRepository,
        knowledge: KnowledgeRepository,
        settings: Settings,
        security: ContentSecurityService | None = None,
    ) -> None:
        self._repository = repository
        self._knowledge = knowledge
        self._settings = settings
        self._security = security

    async def build(
        self,
        *,
        case: Any,
        run: Any,
        history: list[LLMMessage],
        skill_catalog: str,
    ) -> BuiltContext:
        case_info = self._case_info(case, run, skill_catalog)
        ui_context_block = self._ui_context_block(run)
        try:
            # M23: 普通上下文只检索 active 记忆（expired/disabled/deleted/
            # pending_review 按策略不进入）。
            memories = await self._knowledge.list_memories(
                case.id, status=MEMORY_STATUS_ACTIVE
            )
            artifacts = await self._repository.list_artifacts(case.id)
        except Exception:
            return BuiltContext(
                system_context=self._join(case_info, ui_context_block),
                history_window=list(history),
                stats={"degraded": True, "reason": "context_lookup_failed"},
            )

        constraints = [m for m in memories if m.kind == "constraint"]
        candidates = [
            m
            for m in memories
            if m.kind not in {"constraint", "summary"} and m.importance >= 0.7
        ]
        summaries = [m for m in memories if m.kind == "summary"]
        latest_summary = (
            max(summaries, key=lambda m: m.updated_at) if summaries else None
        )

        # M09: 人工审核决策作为高优先级上下文注入（来源 human_review）。
        review_block = await self._human_review_block(case.id)

        budget = self._settings.context_token_budget
        fixed = self._join(
            case_info,
            ui_context_block,
            self._constraint_block(constraints),
            review_block,
        )
        remaining = budget - _estimate_tokens(fixed)

        memory_block = await self._memory_block(candidates, remaining)
        remaining -= _estimate_tokens(memory_block)

        artifact_block = self._fit_lines(
            [self._artifact_line(a) for a in artifacts],
            remaining,
        )
        remaining -= _estimate_tokens(artifact_block)

        summary_block = ""
        if latest_summary is not None:
            summary_block = f"\n历史对话摘要：\n{latest_summary.content}"
            if _estimate_tokens(summary_block) > remaining:
                summary_block = _truncate_to_budget(summary_block, remaining)
            remaining -= _estimate_tokens(summary_block)

        window, window_text = self._history_window(
            history,
            max_turns=self._settings.context_history_turns,
            budget_tokens=remaining,
        )

        system_context = self._join(
            fixed,
            memory_block,
            artifact_block,
            summary_block,
            window_text,
        )
        stats = {
            "constraint_count": len(constraints),
            "memory_count": len(candidates),
            "artifact_count": len(artifacts),
            "history_turns": len(window),
            "summary_used": latest_summary is not None,
            "estimated_tokens": _estimate_tokens(system_context),
            "trust_levels": sorted(
                {self._trust_for_source(str(m.source_type)) for m in candidates}
            ),
            "memory_types": sorted(
                {
                    str(getattr(m, "memory_type", "") or m.kind)
                    for m in candidates
                }
            ),
            "security_guard": self._security is not None,
        }
        # M23: 记录本次回答使用的记忆版本（访问审计，失败不阻断）。
        used_ids = [str(m.id) for m in candidates]
        if used_ids:
            try:
                for memory_id in used_ids:
                    await self._knowledge.add_access_event(
                        memory_id,
                        run_id=getattr(run, "id", None),
                        purpose="context",
                    )
            except Exception:  # noqa: BLE001
                pass
        return BuiltContext(
            system_context=system_context,
            history_window=window,
            stats=stats,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _join(*blocks: str) -> str:
        return "\n".join(block for block in blocks if block).strip()

    @staticmethod
    def _estimate(text: str) -> int:
        return _estimate_tokens(text)

    @staticmethod
    def _ui_context_block(run: Any) -> str:
        """M2.2: 把 Run metadata 中的结构化 ui_context 格式化为独立 system 块。

        只描述"用户正在看什么"，不构成事实证据；事实内容必须经工具查询。
        不按 selected_id 做任何数据库加载，避免跨 case 读取。
        """
        metadata = getattr(run, "metadata_json", None) or {}
        ui_context = (
            metadata.get("ui_context")
            if isinstance(metadata, dict)
            else None
        )
        if not isinstance(ui_context, dict) or not ui_context:
            return ""
        lines = [
            "当前界面导航上下文（仅用于理解用户正在查看的对象，不构成事实证据）：",
            f"- 工作区：{ui_context.get('workspace', 'unknown')}",
        ]
        selected_type = ui_context.get("selected_type")
        selected_id = ui_context.get("selected_id")
        if selected_type or selected_id:
            label = ui_context.get("selected_label")
            descriptor = " / ".join(
                part for part in (str(selected_type or ""), str(selected_id or "")) if part
            )
            if label:
                descriptor = f"{descriptor}（{label}）"
            lines.append(f"- 当前选中对象：{descriptor}")
        filters = ui_context.get("filters")
        if isinstance(filters, dict) and filters:
            lines.append(
                "- 当前过滤条件："
                + json.dumps(filters, ensure_ascii=False, sort_keys=True)
            )
        time_range = ui_context.get("time_range")
        if isinstance(time_range, dict) and (time_range.get("start") or time_range.get("end")):
            lines.append(
                f"- 时间范围：{time_range.get('start') or '未限定'} ~ "
                f"{time_range.get('end') or '未限定'}"
            )
        lines.append(
            "若需要该对象的事实内容，必须调用允许的工具查询，"
            "并仍遵守 Evidence ID 引用规则。"
        )
        return "\n".join(lines)

    @staticmethod
    def _case_info(case: Any, run: Any, skill_catalog: str) -> str:
        dispatch_context = getattr(run, "metadata_json", None) or {}
        dispatch = dispatch_context.get("dispatch") if isinstance(
            dispatch_context, dict
        ) else None
        return (
            f"案例 ID：{case.id}\n案例：{case.title}\n主题：{case.topic}\n"
            f"平台：{case.platforms}\n时间范围：{case.time_range}\n"
            + (
                f"父 Agent 委派上下文："
                f"{json.dumps(dispatch, ensure_ascii=False)}\n"
                if dispatch is not None
                else ""
            )
            + f"可按需加载的 Skill 目录：{skill_catalog}"
        )

    async def _human_review_block(self, case_id: str) -> str:
        """M09: 已接受/已拒绝的人工审核决策注入（来源 human_review）。

        任何失败静默降级为空块，不阻断上下文构建。
        """
        try:
            items = await self._repository.list_review_items(
                case_id, status=None, limit=100
            )
        except Exception:
            return ""
        decided = [
            item
            for item in items
            if item.status in {"accepted", "rejected"}
        ]
        if not decided:
            return ""
        lines = [
            (
                f"- [人工{'确认' if item.status == 'accepted' else '排除'}·{item.object_type}] "
                f"{item.object_id}（{item.summary[:80]}）"
            )
            for item in decided
        ]
        return (
            "人工审核结论（来源 human_review，优先级高于模型推断，不可违反）：\n"
            + "\n".join(lines)
        )

    @staticmethod
    def _constraint_block(constraints: list[Any]) -> str:
        lines = [
            f"- [约束] {m.content}（来源：{m.source_type}:{m.source_id}）"
            for m in constraints
        ]
        if not lines:
            return ""
        return "用户确认的关键约束（不可违反）：\n" + "\n".join(lines)

    async def _memory_block(
        self,
        memories: list[Any],
        budget_tokens: int,
    ) -> str:
        """M16: 记忆以带信任标签的数据块进入上下文；外部来源内容先过
        ContextPolicy（高风险隔离、中风险截断），保留证据可查看。"""
        lines: list[str] = []
        for memory in memories:
            if (
                self._security is not None
                and self._trust_for_source(memory.source_type)
                == TRUST_EXTERNAL_CONTENT
            ):
                envelope = ContentEnvelope(
                    content=str(memory.content),
                    source_type=str(memory.source_type),
                    source_id=str(memory.source_id),
                    trust=TRUST_EXTERNAL_CONTENT,
                    review_state=(
                        "accepted"
                        if str(memory.metadata_json or {}).find("accepted") >= 0
                        else "unreviewed"
                    ),
                )
                text, _assessment = await self._security.context_policy(
                    envelope,
                    object_type="memory",
                    object_id=str(memory.id),
                )
                lines.append(
                    f"- [{memory.kind}|external_content] {text}"
                    f"（来源：{memory.source_type}:{memory.source_id}）"
                )
            else:
                lines.append(self._memory_line(memory))
        return self._fit_lines(lines, budget_tokens)

    @staticmethod
    def _trust_for_source(source_type: str) -> str:
        """M16: 来源到信任等级的确定性映射（外部内容不可自我提升）。"""
        if source_type in _EXTERNAL_SOURCE_TYPES:
            return TRUST_EXTERNAL_CONTENT
        if source_type == "conversation":
            return TRUST_GENERATED_CONTENT
        if source_type == "constraint":
            return TRUST_OPERATOR_INPUT
        return TRUST_REVIEWED_EVIDENCE

    @staticmethod
    def _memory_line(memory: Any) -> str:
        trust = ContextBuilder._trust_for_source(str(memory.source_type))
        memory_type = str(getattr(memory, "memory_type", "") or memory.kind)
        prefix = memory.kind
        if memory_type == MEMORY_TYPE_CASE_HYPOTHESIS:
            prefix = "case_hypothesis"
            # M23: 推测使用推测措辞，不得表述为事实。
            content = "（推测）" + str(memory.content)
        elif memory_type == MEMORY_TYPE_CONVERSATION_SUMMARY:
            prefix = "conversation_summary"
            content = str(memory.content) + "（" + summary_tag(memory_type) + "）"
        else:
            content = str(memory.content)
        if getattr(memory, "confidence", 1) < 0.7:
            content = "（低置信）" + content
        return (
            f"- [{prefix}|{trust}] {content}"
            f"（来源：{memory.source_type}:{memory.source_id}）"
        )

    @staticmethod
    def _artifact_line(artifact: Any) -> str:
        return (
            f"- {artifact.kind} v{artifact.version}: {artifact.title} "
            f"（{artifact.id}）"
        )

    @classmethod
    def _fit_lines(cls, lines: list[str], budget_tokens: int) -> str:
        """Fit as many lines as the budget allows; drop the section entirely
        when even the first line exceeds it (budget applies from the first
        candidate, so a tiny budget empties the whole section)."""
        block = ""
        for line in lines:
            candidate = f"{block}\n{line}".strip()
            if cls._estimate(candidate) > budget_tokens:
                break
            block = candidate
        if not block:
            return ""
        return f"相关记忆：\n{block}"

    def _history_window(
        self,
        history: list[LLMMessage],
        *,
        max_turns: int,
        budget_tokens: int,
    ) -> tuple[list[LLMMessage], str]:
        window: list[LLMMessage] = []
        used = 0
        for message in reversed(history):
            cost = _estimate_tokens(message.content or "")
            if window and used + cost > budget_tokens:
                break
            window.insert(0, message)
            used += cost
            if len(window) >= max_turns:
                break
        lines = "\n".join(
            f"[{'用户' if m.role == 'user' else '助手'}] {m.content}" for m in window
        )
        text = f"\n最近对话（共 {len(window)} 轮）：\n{lines}" if window else ""
        return window, text
