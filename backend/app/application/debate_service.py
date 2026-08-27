"""四轮辩论引擎（Debate Service）。

以各平台采集数据为背景知识，多角色扮演辩论逼近事实结论：

- R1 观点陈述：每个平台角色基于本平台帖子数据陈述判断
- R2 互相反驳：各角色指出其他平台视角的证据漏洞与信息偏差
- R3 观点投票：各角色投票给"最接近事实的平台立场"并说明理由
- R4 主持人总结：综合全部发言与投票，给出参考结论

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


class DebateService:
    def __init__(
        self,
        repository: ApplicationRepository,
        social: SocialRepository,
        llm: LLMGateway,
        profiles: PlatformProfileService | None = None,
    ) -> None:
        self._repository = repository
        self._social = social
        self._llm = llm
        # 平台画像记忆：发言时注入平台/用户特点，辩论结束后回写更新。
        self._profiles = profiles

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
                    debate_id, case, history, votes
                )
            else:
                await self._run_role_round(
                    debate_id, case, roles, current_round, posts, history
                )

        next_round = current_round + 1
        if next_round > _MAX_ROUND:
            debate = await self._repository.update_debate(
                debate_id, status="completed"
            )
            # 辩论完成：结合各平台发言、主持人结论与本次采集帖子，
            # 对平台画像记忆做一次回写更新（失败不影响辩论结果）。
            if self._profiles is not None:
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
    ) -> None:
        instruction = _ROUND_INSTRUCTIONS[round]
        history_block = self._history_block(history, [], round)

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

    async def _run_moderator(
        self,
        debate_id: str,
        case: Any,
        history: Sequence[Any],
        votes: Sequence[Any],
    ) -> None:
        history_block = self._history_block(history, votes, 4)
        content = await self._complete(
            "你是舆情辩论主持人，中立客观。",
            f"{history_block}\n\n{_ROUND_INSTRUCTIONS[4]}",
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


def asyncio_gather(*coros: Any) -> Any:
    """Module-level gather indirection so tests can monkeypatch it."""
    import asyncio

    return asyncio.gather(*coros)
