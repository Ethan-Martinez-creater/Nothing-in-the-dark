"""四轮辩论引擎（Debate Service）。

两种模式（``DebateRecord.mode``）：

- ``case_debate``（legacy 全案辩论）：以各平台采集数据为背景知识，
  多角色扮演辩论逼近事实结论——R1 观点陈述 / R2 互相反驳 /
  R3 观点投票（投给平台）/ R4 主持人总结。
- ``finding_challenge``（Finding 对抗性审查）：针对具体 Finding 的
  有证据约束的多 Agent 对抗性审查——R1 独立审查 / R2 交叉质疑 /
  R3 Finding verdict 投票（supported/refuted/insufficient/overreach）/
  R4 主持人综合。Finding 是待审查命题，不是系统真相；Debate 输出
  仅辅助人工审核，不自动修改 Finding 状态，也不是 Evidence。

每轮之间用户可插话（``add_user_message``）；轮次由用户触发
``advance`` 推进（human-in-the-loop），重复触发幂等。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from app.application.platform_profile import (
    PLATFORM_NAMES,
    PlatformProfileService,
)
from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.llm import LLMGateway, LLMMessage, ModelRoute

logger = logging.getLogger(__name__)

ROUND_LABELS = {1: "观点陈述", 2: "互相反驳", 3: "观点投票", 4: "主持人总结"}
_MAX_ROUND = 4

FINDING_CHALLENGE_MODE = "finding_challenge"
FINDING_CHALLENGE_PROMPT_VERSION = "finding_challenge_v1"
# 进入 LLM prompt 的 Evidence 限流（计划文档 M2.4）
_SNAPSHOT_MAX_EVIDENCE = 12
_SNAPSHOT_EXCERPT_LIMIT = 550

# Finding Challenge R3/R4 的合法 verdict（计划文档 M3.3）
FINDING_VERDICTS = ("supported", "refuted", "insufficient", "overreach")
VERDICT_LABELS = {
    "supported": "支持",
    "refuted": "反驳",
    "insufficient": "证据不足",
    "overreach": "过度推断",
}

_SYSTEM_TEMPLATE = (
    "你是「{platform_label}」平台视角的舆情辩论参与者，"
    "代表该平台用户的信息环境与立场。"
    "你只依据以下该平台采集到的帖子数据发言，不虚构证据。\n\n"
    "【{platform_label}平台采集的帖子】\n"
    "{posts}\n\n"
    "【事件背景】\n"
    "{case_title}\n"
)
_ROUND_INSTRUCTIONS = {
    1: (
        "【第 1 轮 · 观点陈述】基于你掌握的平台信息，陈述你对该事件最接近事实的判断："
        "该事件的核心事实是什么、当前最可信的说法是什么、你的平台数据支持哪些结论。"
        "用中文，300 字以内。"
    ),
    2: (
        "【第 2 轮 · 互相反驳】以下是其他平台视角的发言（含用户插话）。"
        "请指出其他平台观点中与你掌握证据冲突或证据不足的地方，"
        "并重申你的判断。用中文，300 字以内。"
    ),
    3: (
        "【第 3 轮 · 观点投票】综合全部发言与证据，投票给\"最接近事实的平台立场\"。"
        "只输出 JSON：{{\"choice\": \"平台英文名\", \"reason\": \"投票理由（中文，150字内）\"}}"
    ),
    4: (
        "【第 4 轮 · 主持人总结】你是辩论主持人。综合各平台角色的发言、用户插话与投票结果，"
        "给出接近事实的参考结论：1) 各方共识点；2) 主要分歧及证据状况；3) 最终参考结论。"
        "用中文，400 字以内。"
    ),
}

_CHALLENGE_SYSTEM_TEMPLATE = (
    "你是「{platform_label}」平台视角的对抗性审查参与者。"
    "你正在审查的是一条【待审查命题】（Finding），不是系统真相；"
    "你的职责是主动暴露它的证据薄弱点、反例、推理漏洞和替代解释。\n"
    "你只能将本平台采集数据与【已关联 Evidence】作为证据，"
    "不得把其他参与者的发言当作证据，不虚构证据。\n\n"
    "【正在挑战的 Finding】\n"
    "Finding ID: {finding_id}\n"
    "类型: {finding_kind}\n"
    "标题: {finding_title}\n"
    "陈述: {finding_statement}\n"
    "发起时状态: {finding_status}\n"
    "发起时置信度: {finding_confidence}\n\n"
    "【已关联 Evidence】\n"
    "{evidence_block}\n\n"
    "【{platform_label}平台采集的帖子】\n"
    "{posts}\n\n"
    "【事件背景】\n"
    "{case_title}\n"
)
_CHALLENGE_ROUND_INSTRUCTIONS = {
    1: (
        "【第 1 轮 · 独立审查】基于本平台数据与已关联 Evidence，独立审查该 Finding：\n"
        "1) 本平台数据支持结论的哪些部分；2) 哪些部分证据不足；"
        "3) 是否与你的数据直接冲突；4) 是否存在过度推断。\n"
        "正文最后单独一行输出 JSON："
        "{{\"tendency\": \"supported|refuted|insufficient|overreach\"}}（当前倾向）。\n"
        "用中文，300 字以内。"
    ),
    2: (
        "【第 2 轮 · 交叉质疑】以下是其他参与者的第 1 轮审查发言（含用户插话）。请：\n"
        "1) 找出最值得质疑的推理；2) 指出遗漏的反例；3) 检查相关性与因果混淆；"
        "4) 提供替代解释。对方证据更强时允许修正你的立场——目标是发现真实分歧，不是制造冲突。\n"
        "用中文，300 字以内。"
    ),
    3: (
        "【第 3 轮 · Finding verdict 投票】对该 Finding 本身投票（不是投给某个平台）。"
        "只输出 JSON：{{\"choice\": \"supported|refuted|insufficient|overreach\", "
        "\"reason\": \"投票理由（中文，150字内）\"}}"
    ),
    4: (
        "【第 4 轮 · 主持人综合】你是对抗性审查主持人。综合全部审查发言、用户插话与 verdict 投票，"
        "严格按以下结构输出（Markdown）：\n"
        "### 共识\n...\n\n### 主要反证与冲突\n...\n\n### 证据缺口\n...\n\n### 替代解释\n...\n\n"
        "### 建议的复核态度\nsupported / refuted / insufficient / overreach\n理由：...\n\n"
        "> 本结果仅用于辅助人工审核，不自动修改 Finding 状态。\n"
        "你是综合者，不是终审裁判。用中文，450 字以内。"
    ),
}


class DebateService:
    def __init__(
        self,
        repository: ApplicationRepository,
        social: SocialRepository,
        llm: LLMGateway,
        profiles: PlatformProfileService | None = None,
        finding_service: Any | None = None,
    ) -> None:
        self._repository = repository
        self._social = social
        self._llm = llm
        # 平台画像记忆：发言时注入平台/用户特点，case_debate 结束后回写更新。
        self._profiles = profiles
        # Finding Service：finding_challenge 的 Finding 校验与 snapshot 构建。
        self._finding_service = finding_service

    # ---------- 生命周期 ----------

    async def create_debate(self, case_id: str, title: str | None) -> Any:
        case = await self._repository.get_case(case_id)
        platforms = list(case.platforms or [])
        if not platforms:
            raise ApplicationError(
                "case has no platforms to debate", code="debate_no_platforms"
            )
        # 辩论必须以采集数据为依据：至少一个平台有入库帖子才允许发起。
        posts = await self._social.list_posts_by_case(case_id)
        if not posts:
            raise ApplicationError(
                "case has no collected posts to debate", code="debate_no_data"
            )
        debate = await self._repository.create_debate(
            case_id,
            title=title or "多平台观点辩论",
            platform_roles=platforms,
        )
        return debate

    async def create_finding_challenge(
        self, case_id: str, finding_id: str
    ) -> tuple[Any, bool]:
        """针对具体 Finding 的对抗性审查（create-or-resume）。

        同一 Finding 同时只允许一个进行中的 Challenge：已存在则直接返回
        ``(现有 debate, False)``。Finding 上下文由服务端构建并固化为
        context_snapshot。
        """
        if self._finding_service is None:
            raise ApplicationError(
                "finding challenge requires finding service",
                code="finding_challenge_unavailable",
            )
        # 跨 case / 不存在 → finding_scope_mismatch / finding_not_found
        finding = await self._finding_service.get_for_case(case_id, finding_id)
        case = await self._repository.get_case(case_id)
        platforms = list(case.platforms or [])
        if not platforms:
            raise ApplicationError(
                "case has no platforms to debate", code="debate_no_platforms"
            )
        active = await self._repository.get_active_debate_for_finding(
            case_id, finding_id
        )
        if active is not None:
            return active, False
        snapshot = await self._build_finding_snapshot(case_id, finding_id)
        debate = await self._repository.create_debate(
            case_id,
            title=f"对抗性审查：{finding.title}"[:200],
            platform_roles=platforms,
            mode=FINDING_CHALLENGE_MODE,
            finding_id=finding_id,
            context_snapshot=snapshot,
        )
        return debate, True

    async def _build_finding_snapshot(
        self, case_id: str, finding_id: str
    ) -> dict[str, object]:
        """服务端构建 Finding 上下文快照（M2.3），创建后不再变化。

        Evidence 正文解析失败只保留 ref/relation，不阻止 Challenge（M2.4）。
        """
        detail = await self._finding_service.detail(case_id, finding_id)
        finding = detail["finding"]
        evidence_items: list[dict[str, object]] = []
        for link in list(detail["evidence_links"])[:_SNAPSHOT_MAX_EVIDENCE]:
            entry: dict[str, object] = {
                "evidence_ref": link.evidence_ref,
                "relation": link.relation,
            }
            try:
                record = await self._repository.get_evidence_for_case(
                    case_id, link.evidence_ref
                )
                if record is not None:
                    entry["excerpt"] = str(record.excerpt or "")[
                        :_SNAPSHOT_EXCERPT_LIMIT
                    ]
            except Exception:
                logger.warning(
                    "evidence %s lookup failed during snapshot build",
                    link.evidence_ref,
                    exc_info=True,
                )
            evidence_items.append(entry)
        sources = [
            {
                "source_type": link.source_type,
                "source_id": link.source_id,
                "source_path": link.source_path,
            }
            for link in detail["sources"]
        ]
        return {
            "prompt_version": FINDING_CHALLENGE_PROMPT_VERSION,
            "finding": {
                "id": finding.id,
                "kind": finding.kind,
                "title": finding.title,
                "statement": finding.statement,
                "status": finding.status,
                "confidence": finding.confidence,
            },
            "evidence": evidence_items,
            "sources": sources,
        }

    async def add_user_message(self, debate_id: str, content: str) -> Any:
        debate = await self._repository.get_debate(debate_id)
        return await self._repository.add_debate_message(
            debate_id,
            role="user",
            round=debate.round,
            content=content,
        )

    async def advance(self, debate_id: str) -> Any:
        """生成当前轮的发言并进入下一轮；重复调用幂等。"""
        debate = await self._repository.get_debate(debate_id)
        if debate.status != "in_progress":
            raise ApplicationError(
                "debate already completed", code="debate_completed"
            )
        current_round = debate.round
        roles = list((debate.platform_roles or {}).get("platforms") or [])
        is_challenge = debate.mode == FINDING_CHALLENGE_MODE

        if current_round > _MAX_ROUND:
            raise ApplicationError(
                "debate has no more rounds", code="debate_no_more_rounds"
            )

        # 幂等：当前轮已有平台角色发言则直接进入下一轮。
        already = await self._repository.has_debate_round_roles(
            debate_id, current_round
        )
        if not already:
            case = await self._repository.get_case(debate.case_id)
            posts = await self._social.list_posts_by_case(case.id)
            history = await self._repository.list_debate_messages(debate_id)
            votes = await self._repository.list_debate_votes(debate_id)

            if current_round == 4:
                await self._run_moderator(
                    debate_id, case, history, votes, is_challenge
                )
            else:
                await self._run_role_round(
                    debate_id,
                    case,
                    roles,
                    current_round,
                    posts,
                    history,
                    is_challenge=is_challenge,
                    snapshot=(debate.context_snapshot or {}),
                )

        next_round = current_round + 1
        if next_round > _MAX_ROUND:
            debate = await self._repository.update_debate(
                debate_id, status="completed"
            )
            # 辩论完成：结合各平台发言、主持人结论与本次采集帖子，
            # 对平台画像记忆做一次回写更新（失败不影响辩论结果）。
            # M3.7：finding_challenge 的合成对抗性发言是任务驱动推理，
            # 不是自然平台行为，禁止回写长期平台画像。
            if self._profiles is not None and not is_challenge:
                try:
                    case = await self._repository.get_case(debate.case_id)
                    posts = await self._social.list_posts_by_case(debate.case_id)
                    messages = await self._repository.list_debate_messages(debate_id)
                    await self._profiles.refresh_from_debate(
                        roles, posts, messages, topic=case.title
                    )
                except Exception:
                    logger.warning(
                        "platform profile debate refresh failed",
                        exc_info=True,
                    )
        else:
            debate = await self._repository.update_debate(
                debate_id, round=next_round
            )
        return debate

    # ---------- 内部 ----------

    def _platform_posts(
        self, posts: Sequence[Any], platform: str
    ) -> list[str]:
        lines: list[str] = []
        for post in posts:
            if str(post.platform) != platform:
                continue
            time = (
                post.published_at.isoformat()[:16]
                if post.published_at
                else "?"
            )
            engagement = post.engagement
            if isinstance(engagement, dict):
                engagement = engagement.get("total") or sum(
                    v for v in engagement.values() if isinstance(v, (int, float))
                ) or 0
            raw = post.raw_payload or {}
            sentiment = raw.get("sentiment") or "—"
            lines.append(
                f"- [{time}] {str(post.content)[:120]}"
                f"（互动 {engagement}，情感 {sentiment}）"
            )
        return lines[:12]

    def _history_block(
        self,
        history: Sequence[Any],
        votes: Sequence[Any],
        current_round: int,
        *,
        is_challenge: bool = False,
    ) -> str:
        """前序轮次与当前轮用户插话的完整记录（供模型参考）。"""
        lines: list[str] = []
        for message in history:
            role_label = {
                "platform_role": PLATFORM_NAMES.get(
                    str(message.platform or ""), str(message.platform)
                ),
                "user": "用户",
                "moderator": "主持人",
            }.get(str(message.role), str(message.role))
            lines.append(
                f"[第{message.round}轮 · {role_label}] {message.content}"
            )
        for vote in votes:
            if is_challenge:
                # Finding verdict：显示对 Finding 的判定，不是"投给某平台"。
                label = VERDICT_LABELS.get(str(vote.choice), str(vote.choice))
                lines.append(
                    f"[第3轮投票 · {PLATFORM_NAMES.get(str(vote.platform), vote.platform)}]"
                    f" 判定 {label}：{vote.reason}"
                )
            else:
                lines.append(
                    f"[第3轮投票 · {PLATFORM_NAMES.get(str(vote.platform), vote.platform)}]"
                    f" 投给 {PLATFORM_NAMES.get(str(vote.choice), vote.choice)}：{vote.reason}"
                )
        if not lines:
            return "（暂无历史发言）"
        return "\n".join(lines)

    async def _complete(
        self, system: str, user: str
    ) -> str:
        try:
            response = await self._llm.complete(
                messages=[
                    LLMMessage(role="system", content=system),
                    LLMMessage(role="user", content=user),
                ],
                tools=[],
                route=ModelRoute.FAST,
            )
            return (response.message.content or "").strip()
        except Exception:
            logger.exception("debate LLM call failed")
            return "（本角色本轮未能生成发言）"

    async def _run_role_round(
        self,
        debate_id: str,
        case: Any,
        roles: list[str],
        round: int,
        posts: Sequence[Any],
        history: Sequence[Any],
        *,
        is_challenge: bool = False,
        snapshot: dict[str, object] | None = None,
    ) -> None:
        if is_challenge:
            instruction = _CHALLENGE_ROUND_INSTRUCTIONS[round]
        else:
            instruction = _ROUND_INSTRUCTIONS[round]
        history_block = self._history_block(
            history, [], round, is_challenge=is_challenge
        )

        async def speak(platform: str) -> None:
            platform_lines = self._platform_posts(posts, platform)
            platform_label = PLATFORM_NAMES.get(platform, platform)
            if not platform_lines:
                # 本平台无采集数据：不调 LLM 编造观点，落一条明确声明，
                # 且不参与本轮投票（R3）。
                await self._repository.add_debate_message(
                    debate_id,
                    role="platform_role",
                    round=round,
                    platform=platform,
                    content=(
                        f"【数据缺失】{platform_label}平台尚未采集到帖子数据，"
                        "无法基于证据参与本轮辩论（不陈述观点、不投票）。"
                        "请先完成该平台的数据采集，再重新发起辩论。"
                    ),
                )
                return
            posts_text = "\n".join(platform_lines)
            if is_challenge:
                system = self._challenge_system_prompt(
                    platform_label, posts_text, case, snapshot or {}
                )
            else:
                system = _SYSTEM_TEMPLATE.format(
                    platform_label=platform_label,
                    posts=posts_text,
                    case_title=case.title,
                )
            # 平台画像记忆注入：跨案例累积的平台/用户特点，让发言视角
            # 与措辞更贴近该平台真实生态；结论依据仍以本次采集帖子为准。
            if self._profiles is not None:
                try:
                    profile = await self._profiles.get_profile(platform)
                except Exception:
                    logger.warning(
                        "platform profile lookup failed for %s", platform,
                        exc_info=True,
                    )
                    profile = None
                if profile is not None:
                    system += (
                        "\n【平台画像记忆（跨案例累积观察）】\n"
                        f"{profile.content}\n"
                        "（以上画像可辅助你以该平台的表达习惯组织发言，"
                        "但观点依据仍必须来自上方本次采集的帖子。）"
                    )
            content = await self._complete(
                system,
                f"{history_block}\n\n{instruction}",
            )
            if round == 3:
                if is_challenge:
                    choice, reason = _parse_verdict(content)
                    await self._repository.add_debate_vote(
                        debate_id,
                        platform=platform,
                        choice=choice,
                        reason=reason,
                    )
                    label = VERDICT_LABELS.get(choice, choice)
                    message = f"投票：{label}。理由：{reason}"
                else:
                    choice, reason = _parse_vote(content)
                    await self._repository.add_debate_vote(
                        debate_id,
                        platform=platform,
                        choice=choice or platform,
                        reason=reason,
                    )
                    message = (
                        f"投票：支持「{PLATFORM_NAMES.get(choice, choice)}」的立场。"
                        f"理由：{reason}"
                        if choice
                        else content
                    )
            else:
                message = content
            await self._repository.add_debate_message(
                debate_id,
                role="platform_role",
                round=round,
                platform=platform,
                content=message,
            )

        await asyncio_gather(*[speak(platform) for platform in roles])

    def _challenge_system_prompt(
        self,
        platform_label: str,
        posts_text: str,
        case: Any,
        snapshot: dict[str, object],
    ) -> str:
        """Finding Challenge 的 system prompt（M3.5）：Finding 上下文 +
        已关联 Evidence + 本平台采集数据。"""
        payload = snapshot or {}
        finding = dict(payload.get("finding") or {})
        evidence_items = list(payload.get("evidence") or [])
        if evidence_items:
            evidence_lines = []
            for item in evidence_items:
                excerpt = str(item.get("excerpt") or "").strip()
                entry = (
                    f"- ref={item.get('evidence_ref')} "
                    f"relation={item.get('relation')}"
                )
                if excerpt:
                    entry += f"\n  摘录：{excerpt}"
                evidence_lines.append(entry)
            evidence_block = "\n".join(evidence_lines)
        else:
            evidence_block = "（该 Finding 暂无已关联 Evidence）"
        return _CHALLENGE_SYSTEM_TEMPLATE.format(
            platform_label=platform_label,
            posts=posts_text,
            case_title=case.title,
            finding_id=finding.get("id", "?"),
            finding_kind=finding.get("kind", "?"),
            finding_title=finding.get("title", "?"),
            finding_statement=finding.get("statement", "?"),
            finding_status=finding.get("status", "?"),
            finding_confidence=finding.get("confidence", "?"),
            evidence_block=evidence_block,
        )

    async def _run_moderator(
        self,
        debate_id: str,
        case: Any,
        history: Sequence[Any],
        votes: Sequence[Any],
        is_challenge: bool = False,
    ) -> None:
        history_block = self._history_block(
            history, votes, 4, is_challenge=is_challenge
        )
        if is_challenge:
            system = (
                "你是对抗性审查主持人，中立客观。"
                "正在综合的是针对一条待审查命题（Finding）的多方审查结果。"
                "你是综合者，不是终审裁判。"
            )
            instruction = _CHALLENGE_ROUND_INSTRUCTIONS[4]
        else:
            system = "你是舆情辩论主持人，中立客观。"
            instruction = _ROUND_INSTRUCTIONS[4]
        content = await self._complete(
            system,
            f"{history_block}\n\n{instruction}",
        )
        await self._repository.add_debate_message(
            debate_id,
            role="moderator",
            round=4,
            content=content,
        )


def _parse_vote(content: str) -> tuple[str | None, str]:
    """从角色发言中解析 R3 投票 JSON；失败时返回 (None, 原文)。"""
    try:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            payload = json.loads(content[start : end + 1])
            choice = str(payload.get("choice") or "").strip()
            reason = str(payload.get("reason") or "").strip()
            if choice:
                return choice, reason or "（未说明理由）"
    except (ValueError, TypeError):
        logger.warning("vote JSON parse failed: %s", content[:120])
    return None, content


def _parse_verdict(content: str) -> tuple[str, str]:
    """Finding Challenge R3：解析 verdict JSON。

    非法 choice 禁止 fallback 到平台名；fail-safe 保守归为
    ``insufficient`` 并保留解析说明（计划文档 M3.3）。
    """
    try:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            payload = json.loads(content[start : end + 1])
            choice = str(payload.get("choice") or "").strip()
            reason = str(payload.get("reason") or "").strip()
            if choice in FINDING_VERDICTS:
                return choice, reason or "（未说明理由）"
            if choice:
                logger.warning("invalid finding verdict choice: %s", choice)
                return "insufficient", (
                    f"（模型输出非法 verdict '{choice}'，保守归为证据不足）"
                    f"{reason or content[:150]}"
                )
    except (ValueError, TypeError):
        logger.warning("verdict JSON parse failed: %s", content[:120])
    return "insufficient", (
        f"（输出无法解析为合法 verdict，保守归为证据不足）原文：{content[:150]}"
    )


def asyncio_gather(*coros: Any) -> Any:
    """Module-level gather indirection so tests can monkeypatch it."""
    import asyncio

    return asyncio.gather(*coros)
