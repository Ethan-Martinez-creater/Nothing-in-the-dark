"""平台画像记忆（Platform Profile Memory）。

跨案例共享的「平台特点 / 平台用户特点」画像，供辩论等场景注入上下文：

- 采集入库后（``refresh_from_posts``）：LLM 从本次平台帖子总结平台与
  用户特点；已有画像时由 LLM 比较新观察决定是否更新（平台风格会随时间
  变化），更新走 ``supersedes`` 修订链，旧版本自动失效；
- 辩论结束时（``refresh_from_debate``）：结合该平台角色的发言、主持人
  结论与帖子样本再总结一次观察并走同一合并流程（辩论本身也是观察源）；
- 辩论发言时（``get_profile``）：读取该平台当前画像注入角色 system 上下文。

画像存为 domain 级记忆（``scope=domain``、``kind=platform_profile``、
``source_id=平台名``），不进入案例上下文与案例检索。所有 LLM 失败或
解析失败均静默跳过，绝不阻断采集或辩论主流程。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from app.harness.structured_output import repair_json_content
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.models import MemoryRecord
from app.infrastructure.llm import LLMGateway, LLMMessage, ModelRoute
from app.schemas.knowledge import CreateMemoryRequest
from app.services.content_security import TRUST_GENERATED_CONTENT

logger = logging.getLogger(__name__)

PROFILE_SCOPE = "domain"
PROFILE_KIND = "platform_profile"
PROFILE_SOURCE_TYPE = "platform_profile"

PLATFORM_NAMES = {
    "weibo": "微博",
    "bilibili": "哔哩哔哩",
    "tieba": "百度贴吧",
    "zhihu": "知乎",
    "douyin": "抖音",
}

_SUMMARY_PROMPT = (
    "你是社交平台画像分析器。基于下面某平台关于某事件的采集帖子，"
    "总结该平台与平台用户的稳定特点。只输出 JSON：\n"
    '{{"platform_traits": "平台内容形态与调性（内容形态、讨论组织方式、'
    '官方/媒体存在感等）", "user_traits": "平台用户群体的表达风格与关注偏好", '
    '"basis": "一句话说明依据"}}\n'
    "要求：只依据帖子样本，不虚构；概括稳定特征而非单帖内容；"
    "每项 80 字以内；中文。\n\n"
    "【平台】{platform_label}\n"
    "【事件】{topic}\n"
    "【帖子样本】\n"
    "{posts}\n"
)

_DEBATE_OBSERVATION_PROMPT = (
    "你是社交平台画像分析器。下面是一场多平台辩论中该平台角色的发言、"
    "主持人结论，以及该平台本次采集的帖子。总结对「该平台特点 / "
    "平台用户特点」的新观察。只输出 JSON：\n"
    '{{"platform_traits": "...", "user_traits": "...", '
    '"basis": "..."}}\n'
    "只依据给定材料，不虚构；每项 80 字以内；中文。\n\n"
    "【平台】{platform_label}\n"
    "【事件】{topic}\n"
    "【该平台角色发言】\n"
    "{role_messages}\n"
    "【主持人结论】\n"
    "{moderator}\n"
    "【帖子样本】\n"
    "{posts}\n"
)

_MERGE_PROMPT = (
    "你是社交平台画像维护器。下面是该平台的历史画像与最新观察"
    "（来自新一次采集或一场辩论）。请判断画像是否需要更新。只输出 JSON：\n"
    '{{"changed": true 或 false, "platform_traits": "更新后的平台特点'
    '（完整画像）", "user_traits": "更新后的用户特点（完整画像）", '
    '"reason": "变化原因，未变化则简述为何维持"}}\n'
    "判断原则：新观察仅印证旧画像时 changed=false；出现新的稳定特征、"
    "或旧描述与新证据冲突时 changed=true，并输出合并新旧信息后的完整画像"
    "（保留仍成立的内容）。每项 80 字以内；中文。\n\n"
    "【平台】{platform_label}\n"
    "【历史画像】\n"
    "{existing}\n"
    "【最新观察】\n"
    "{observation}\n"
)


def _field(post: Mapping[str, object] | Any, key: str, default: Any = "") -> Any:
    """兼容 dict 帖子（采集层）与 ORM 记录（辩论/查询层）取字段。"""
    if isinstance(post, Mapping):
        return post.get(key, default)
    return getattr(post, key, default)


def _format_posts(
    posts: Sequence[Mapping[str, object] | Any], platform: str, limit: int = 12
) -> list[str]:
    lines: list[str] = []
    for post in posts:
        if str(_field(post, "platform", "")) != platform:
            continue
        published = _field(post, "published_at", None)
        time_label = (
            published.isoformat()[:16]
            if hasattr(published, "isoformat")
            else str(published or "?")
        )
        raw = _field(post, "raw_payload", None)
        sentiment = (
            raw.get("sentiment")
            if isinstance(raw, Mapping)
            else _field(post, "sentiment", "—")
        ) or "—"
        engagement = _field(post, "engagement", 0)
        if isinstance(engagement, Mapping):
            engagement = engagement.get("total") or sum(
                value
                for value in engagement.values()
                if isinstance(value, (int, float))
            ) or 0
        content = str(_field(post, "content", ""))[:120]
        lines.append(
            f"- [{time_label}] {content}（互动 {engagement}，情感 {sentiment}）"
        )
    return lines[:limit]


def _profile_content(traits: Mapping[str, Any]) -> str:
    return (
        f"【平台特点】{str(traits.get('platform_traits') or '').strip()}\n"
        f"【平台用户特点】{str(traits.get('user_traits') or '').strip()}"
    )


class PlatformProfileService:
    """平台画像的总结、比较更新与读取（domain 级记忆）。"""

    def __init__(
        self,
        knowledge: KnowledgeRepository,
        llm: LLMGateway,
        governance: Any | None = None,
    ) -> None:
        self._knowledge = knowledge
        # M23: 平台画像为 LLM 生成内容，经治理 Gate 落库（低信任、可审）。
        self._governance = governance
        self._llm = llm

    # ---------- 读取 ----------

    async def get_profile(self, platform: str) -> MemoryRecord | None:
        """读取某平台当前活跃画像（domain 级，跨案例共享）。"""
        records = await self._knowledge.list_memories(
            case_id=None, scope=PROFILE_SCOPE
        )
        for record in records:
            if (
                record.kind == PROFILE_KIND
                and record.source_id == platform
                and record.active
            ):
                return record
        return None

    # ---------- 写入 / 更新 ----------

    async def refresh_from_posts(
        self,
        platforms: Sequence[str],
        posts: Sequence[Mapping[str, object] | Any],
        topic: str = "",
    ) -> dict[str, str]:
        """采集入库后：按平台总结画像并写入或比较更新。

        返回 ``{platform: created|updated|unchanged|skipped}``；
        任何失败只记日志，不抛出。
        """
        statuses: dict[str, str] = {}
        for platform in platforms:
            samples = _format_posts(posts, platform)
            if not samples:
                statuses[platform] = "skipped"
                continue
            try:
                observation = await self._summarize(
                    platform, topic, "\n".join(samples)
                )
                if observation is None:
                    statuses[platform] = "skipped"
                    continue
                statuses[platform] = await self._upsert(
                    platform,
                    observation,
                    basis="collected posts",
                )
            except Exception:
                logger.warning(
                    "platform profile refresh failed for %s", platform, exc_info=True
                )
                statuses[platform] = "skipped"
        return statuses

    async def refresh_from_debate(
        self,
        platforms: Sequence[str],
        posts: Sequence[Mapping[str, object] | Any],
        messages: Sequence[Any],
        topic: str = "",
    ) -> dict[str, str]:
        """辩论结束后：结合平台发言、主持人结论与帖子样本更新画像。"""
        moderator = "\n".join(
            str(message.content) for message in messages if message.role == "moderator"
        )
        statuses: dict[str, str] = {}
        for platform in platforms:
            role_messages = "\n".join(
                f"[第{message.round}轮] {str(message.content)[:160]}"
                for message in messages
                if message.role == "platform_role" and message.platform == platform
            )
            samples = _format_posts(posts, platform)
            try:
                observation = await self._summarize_debate(
                    platform, topic, role_messages, moderator, "\n".join(samples)
                )
                if observation is None:
                    statuses[platform] = "skipped"
                    continue
                statuses[platform] = await self._upsert(
                    platform,
                    observation,
                    basis="debate conclusion",
                )
            except Exception:
                logger.warning(
                    "platform profile debate refresh failed for %s",
                    platform,
                    exc_info=True,
                )
                statuses[platform] = "skipped"
        return statuses

    # ---------- 内部 ----------

    async def _upsert(
        self, platform: str, observation: Mapping[str, Any], *, basis: str
    ) -> str:
        """无画像直接写入；有画像走 LLM 比较合并，必要时 supersedes 更新。"""
        existing = await self.get_profile(platform)
        if existing is None:
            await self._persist_profile(
                platform,
                _profile_content(observation),
                importance=0.8,
                confidence=0.7,
                basis=basis,
            )
            return "created"

        merged = await self._merge(
            platform, existing.content, _profile_content(observation)
        )
        if merged is None:
            return "unchanged"
        changed, content = merged
        if not changed:
            return "unchanged"
        await self._persist_profile(
            platform,
            content,
            importance=0.8,
            confidence=0.75,
            basis=basis,
            supersedes_id=existing.id,
        )
        return "updated"

    async def _persist_profile(
        self,
        platform: str,
        content: str,
        *,
        importance: float,
        confidence: float,
        basis: str,
        supersedes_id: str | None = None,
    ) -> None:
        """写入平台画像记忆；装配 governance 时经 M23 Gate 落库。

        平台画像为 LLM 生成内容（generated_content），不自行提升信任等级；
        治理失败静默跳过（与既有"绝不阻断采集/辩论主流程"一致）。
        """
        request = CreateMemoryRequest(
            scope=PROFILE_SCOPE,
            kind=PROFILE_KIND,
            content=content,
            source_type=PROFILE_SOURCE_TYPE,
            source_id=platform,
            importance=importance,
            confidence=confidence,
            supersedes_id=supersedes_id,
            metadata={"basis": basis},
        )
        if self._governance is not None:
            try:
                await self._governance.persist_governed(
                    case_id=None,
                    request=request,
                    memory_type="case_hypothesis",
                    trust_level=TRUST_GENERATED_CONTENT,
                    has_evidence=True,
                )
                return
            except Exception:  # noqa: BLE001 - 画像失败不阻断主流程
                logger.warning(
                    "platform profile governance persist failed for %s",
                    platform,
                    exc_info=True,
                )
                return
        await self._knowledge.create_memory(case_id=None, request=request)

    async def _complete(self, system: str, user: str) -> str:
        response = await self._llm.complete(
            messages=[
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=user),
            ],
            tools=[],
            route=ModelRoute.FAST,
        )
        return (response.message.content or "").strip()

    async def _summarize(
        self, platform: str, topic: str, posts_text: str
    ) -> dict[str, Any] | None:
        try:
            raw = await self._complete(
                "你是社交平台画像分析器，只输出 JSON。",
                _SUMMARY_PROMPT.format(
                    platform_label=PLATFORM_NAMES.get(platform, platform),
                    topic=topic or "（未提供）",
                    posts=posts_text,
                ),
            )
        except Exception:
            logger.warning(
                "profile summarize LLM failed for %s", platform, exc_info=True
            )
            return None
        payload = repair_json_content(raw)
        if not isinstance(payload, dict) or not payload.get("platform_traits"):
            return None
        return payload

    async def _summarize_debate(
        self,
        platform: str,
        topic: str,
        role_messages: str,
        moderator: str,
        posts_text: str,
    ) -> dict[str, Any] | None:
        try:
            raw = await self._complete(
                "你是社交平台画像分析器，只输出 JSON。",
                _DEBATE_OBSERVATION_PROMPT.format(
                    platform_label=PLATFORM_NAMES.get(platform, platform),
                    topic=topic or "（未提供）",
                    role_messages=role_messages or "（该平台未发言）",
                    moderator=moderator or "（无主持人结论）",
                    posts=posts_text or "（无帖子样本）",
                ),
            )
        except Exception:
            logger.warning(
                "profile debate summarize failed for %s", platform, exc_info=True
            )
            return None
        payload = repair_json_content(raw)
        if not isinstance(payload, dict) or not payload.get("platform_traits"):
            return None
        return payload

    async def _merge(
        self, platform: str, existing_content: str, observation: str
    ) -> tuple[bool, str] | None:
        """LLM 比较旧画像与新观察；返回 (是否更新, 新画像文本)。"""
        try:
            raw = await self._complete(
                "你是社交平台画像维护器，只输出 JSON。",
                _MERGE_PROMPT.format(
                    platform_label=PLATFORM_NAMES.get(platform, platform),
                    existing=existing_content,
                    observation=observation,
                ),
            )
        except Exception:
            logger.warning("profile merge LLM failed for %s", platform, exc_info=True)
            return None
        payload = repair_json_content(raw)
        if not isinstance(payload, dict):
            return None
        changed = bool(payload.get("changed"))
        traits = {
            "platform_traits": payload.get("platform_traits"),
            "user_traits": payload.get("user_traits"),
        }
        return changed, _profile_content(traits)
