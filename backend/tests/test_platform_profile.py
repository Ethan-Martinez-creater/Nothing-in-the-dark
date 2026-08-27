"""平台画像记忆：采集后写入/LLM 比较更新/辩论注入与回写。"""

from __future__ import annotations

import json
from pathlib import Path

from app.application.debate_service import DebateService
from app.application.platform_profile import PlatformProfileService
from app.application.repositories import ApplicationRepository
from app.infrastructure.database import Database
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse
from app.schemas.cases import CreateCaseRequest


class ScriptedGateway(LLMGateway):
    """按 system 提示词标记路由：画像总结 / 画像合并 / 辩论发言。"""

    def __init__(self) -> None:
        self.systems: list[str] = []
        self.users: list[str] = []
        # 可在用例内覆写的响应队列钩子
        self.summary_response = json.dumps(
            {
                "platform_traits": "短视频传播为主，官方账号活跃",
                "user_traits": "情绪化表达，爱用梗图与短评",
                "basis": "12 条帖子样本",
            },
            ensure_ascii=False,
        )
        self.merge_response = json.dumps(
            {
                "changed": True,
                "platform_traits": "短视频传播为主，官方账号活跃，新增辟谣合集形态",
                "user_traits": "情绪化表达，爱用梗图与短评，核查意识增强",
                "reason": "出现新的稳定特征",
            },
            ensure_ascii=False,
        )

    @property
    def configured(self) -> bool:
        return True

    async def complete(self, *, messages: list[LLMMessage], route=None, **kw):
        system = messages[0].content
        user = messages[-1].content
        self.systems.append(system)
        self.users.append(user)
        if "画像分析器" in system:
            return LLMResponse(
                message=LLMMessage(role="assistant", content=self.summary_response),
                model="fake",
            )
        if "画像维护器" in system:
            return LLMResponse(
                message=LLMMessage(role="assistant", content=self.merge_response),
                model="fake",
            )
        if "辩论主持人" in system:
            return LLMResponse(
                message=LLMMessage(
                    role="assistant", content="主持人结论：微博信息更快。"
                ),
                model="fake",
            )
        return LLMResponse(
            message=LLMMessage(role="assistant", content=f"发言：{user[:20]}"),
            model="fake",
        )


_POSTS = [
    {
        "id": "p1",
        "platform": "weibo",
        "author": "a",
        "content": "现场信息汇总，等待官方说明",
        "published_at": "2026-08-16T01:00:00+00:00",
        "sentiment": "neutral",
        "engagement": 100,
        "is_demo": True,
    },
]


async def _setup(tmp_path: Path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'profile.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)
    knowledge = KnowledgeRepository(database)
    gateway = ScriptedGateway()
    profiles = PlatformProfileService(knowledge, gateway)
    return database, repository, social, knowledge, gateway, profiles


async def test_refresh_from_posts_creates_domain_profile(tmp_path: Path) -> None:
    database, repository, social, knowledge, gateway, profiles = await _setup(tmp_path)
    try:
        statuses = await profiles.refresh_from_posts(["weibo"], _POSTS, topic="测试事件")
        assert statuses == {"weibo": "created"}

        records = await knowledge.list_memories(case_id=None, scope="domain")
        matching = [
            r
            for r in records
            if r.kind == "platform_profile" and r.source_id == "weibo"
        ]
        assert len(matching) == 1
        assert "【平台特点】短视频传播为主" in matching[0].content
        assert "【平台用户特点】情绪化表达" in matching[0].content
        assert matching[0].importance == 0.8
    finally:
        await database.dispose()


async def test_existing_profile_updated_via_llm_merge_with_revision_chain(
    tmp_path: Path,
) -> None:
    database, repository, social, knowledge, gateway, profiles = await _setup(tmp_path)
    try:
        await profiles.refresh_from_posts(["weibo"], _POSTS)
        first = await profiles.get_profile("weibo")
        assert first is not None

        # 第二次采集：LLM 判定 changed=true → 走 supersedes 修订链。
        statuses = await profiles.refresh_from_posts(["weibo"], _POSTS)
        assert statuses == {"weibo": "updated"}

        second = await profiles.get_profile("weibo")
        assert second is not None
        assert second.id != first.id
        assert second.supersedes_id == first.id
        assert "核查意识增强" in second.content

        # 旧版本已失效，活跃画像唯一。
        all_records = await knowledge.list_memories(
            case_id=None, scope="domain", include_inactive=True
        )
        first_row = next(r for r in all_records if r.id == first.id)
        assert first_row.active is False
    finally:
        await database.dispose()


async def test_merge_unchanged_keeps_profile(tmp_path: Path) -> None:
    database, repository, social, knowledge, gateway, profiles = await _setup(tmp_path)
    try:
        await profiles.refresh_from_posts(["weibo"], _POSTS)
        first = await profiles.get_profile("weibo")

        gateway.merge_response = json.dumps(
            {
                "changed": False,
                "platform_traits": "（维持）",
                "user_traits": "（维持）",
                "reason": "新观察仅印证旧画像",
            },
            ensure_ascii=False,
        )
        statuses = await profiles.refresh_from_posts(["weibo"], _POSTS)
        assert statuses == {"weibo": "unchanged"}
        assert (await profiles.get_profile("weibo")).id == first.id
    finally:
        await database.dispose()


async def test_bad_llm_output_skips_silently(tmp_path: Path) -> None:
    database, repository, social, knowledge, gateway, profiles = await _setup(tmp_path)
    try:
        gateway.summary_response = "不是 JSON 的输出"
        statuses = await profiles.refresh_from_posts(["weibo"], _POSTS)
        assert statuses == {"weibo": "skipped"}
        assert await profiles.get_profile("weibo") is None
    finally:
        await database.dispose()


async def test_debate_injects_profile_into_platform_context(tmp_path: Path) -> None:
    database, repository, social, knowledge, gateway, profiles = await _setup(tmp_path)
    debate_service = DebateService(repository, social, gateway, profiles=profiles)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="画像注入辩论", platforms=["weibo"])
        )
        await social.persist_batch(case_id=case.id, posts=_POSTS)
        await profiles.refresh_from_posts(["weibo"], _POSTS)

        debate = await debate_service.create_debate(case.id, None)
        await debate_service.advance(debate.id)  # R1

        # 发言的 system 上下文包含画像记忆文本与「仍以帖子为准」的约束。
        system_calls = [s for s in gateway.systems if "辩论参与者" in s]
        assert system_calls, "platform role system prompt missing"
        assert any("平台画像记忆" in s for s in system_calls)
        assert any("短视频传播为主" in s for s in system_calls)
        assert any("本次采集的帖子" in s for s in system_calls)
    finally:
        await database.dispose()


async def test_debate_completion_refreshes_profiles(tmp_path: Path) -> None:
    database, repository, social, knowledge, gateway, profiles = await _setup(tmp_path)
    debate_service = DebateService(repository, social, gateway, profiles=profiles)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="画像回写辩论", platforms=["weibo"])
        )
        await social.persist_batch(case_id=case.id, posts=_POSTS)
        # 采集链路先建立画像，辩论结束后的回写走 LLM 比较更新分支。
        await profiles.refresh_from_posts(["weibo"], _POSTS)
        first = await profiles.get_profile("weibo")

        debate = await debate_service.create_debate(case.id, None)
        for _ in range(4):
            debate = await debate_service.advance(debate.id)
        assert debate.status == "completed"

        # 辩论结束后发生了画像回写：观察请求携带辩论材料（发言+主持人结论），
        # 合并决策 changed=true 后画像经 supersedes 链更新。
        observation_users = [
            u for u in gateway.users if "主持人结论" in u and "该平台角色发言" in u
        ]
        assert observation_users, "debate observation prompt missing"

        refreshed = await profiles.get_profile("weibo")
        assert refreshed is not None
        assert refreshed.id != first.id
        assert refreshed.supersedes_id == first.id
        assert "核查意识增强" in refreshed.content
    finally:
        await database.dispose()
