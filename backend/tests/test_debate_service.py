"""Debate service: four-round flow, user interjection, votes, moderator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.application.debate_service import DebateService
from app.application.repositories import ApplicationRepository
from app.infrastructure.database import Database
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse
from app.schemas.cases import CreateCaseRequest


class ScriptedGateway(LLMGateway):
    """Routes replies by round marker in the user message."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def configured(self) -> bool:
        return True

    async def complete(self, *, messages: list[LLMMessage], route=None, **kw):
        user = messages[-1].content
        self.calls.append(user[:50])
        if "第 3 轮" in user:
            return LLMResponse(
                message=LLMMessage(
                    role="assistant",
                    content=json.dumps(
                        {"choice": "weibo", "reason": "微博有首发信息与时间线"}
                    ),
                ),
                model="fake",
            )
        if "主持人" in messages[0].content:
            return LLMResponse(
                message=LLMMessage(
                    role="assistant",
                    content="综合各方观点：官方通报前存在信息真空，微博首发信息可信度中等。",
                ),
                model="fake",
            )
        return LLMResponse(
            message=LLMMessage(role="assistant", content=f"发言：{user[:30]}"),
            model="fake",
        )


_POSTS = [
    {
        "id": "p1",
        "platform": "weibo",
        "author": "a",
        "content": "暴雨泄洪现场信息，等待官方说明",
        "published_at": "2026-08-07T21:00:00+00:00",
        "sentiment": "negative",
        "engagement": 100,
        "is_demo": True,
    },
    {
        "id": "p2",
        "platform": "bilibili",
        "author": "b",
        "content": "泄洪谣言辟谣时间线视频",
        "published_at": "2026-08-07T22:00:00+00:00",
        "sentiment": "neutral",
        "engagement": 200,
        "is_demo": True,
    },
]


async def _setup(tmp_path: Path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'debate.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)
    gateway = ScriptedGateway()
    service = DebateService(repository, social, gateway)
    return database, repository, social, gateway, service


async def test_four_round_flow_with_votes_and_moderator(tmp_path: Path) -> None:
    database, repository, social, gateway, service = await _setup(tmp_path)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="辩论测试", platforms=["weibo", "bilibili"])
        )
        await social.persist_batch(case_id=case.id, posts=_POSTS)

        debate = await service.create_debate(case.id, "暴雨泄洪辩论")
        assert debate.round == 1
        assert debate.platform_roles == {"platforms": ["weibo", "bilibili"]}

        # R1 陈述
        debate = await service.advance(debate.id)
        messages = await repository.list_debate_messages(debate.id)
        role_msgs = [m for m in messages if m.role == "platform_role"]
        assert len(role_msgs) == 2
        assert {m.platform for m in role_msgs} == {"weibo", "bilibili"}
        assert all(m.round == 1 for m in role_msgs)
        assert debate.round == 2

        # 用户插话
        await service.add_user_message(debate.id, "我认为官方通报更可信")
        messages = await repository.list_debate_messages(debate.id)
        assert any(m.role == "user" and m.content == "我认为官方通报更可信" for m in messages)

        # R2 反驳
        debate = await service.advance(debate.id)
        assert debate.round == 3

        # R3 投票
        debate = await service.advance(debate.id)
        votes = await repository.list_debate_votes(debate.id)
        assert len(votes) == 2
        assert all(v.choice == "weibo" for v in votes)
        assert debate.round == 4

        # R4 主持人总结
        debate = await service.advance(debate.id)
        assert debate.status == "completed"
        messages = await repository.list_debate_messages(debate.id)
        assert any(m.role == "moderator" and m.round == 4 for m in messages)
        # 8 = 2(R1) + 1(用户) + 2(R2) + 2(R3) + 1(主持人)
        assert len(messages) == 8
        assert len(gateway.calls) == 7
    finally:
        await database.dispose()


async def test_advance_never_repeats_a_round(tmp_path: Path) -> None:
    """advance 生成当前轮并推进；每轮角色发言只生成一次。"""
    database, repository, social, gateway, service = await _setup(tmp_path)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="幂等", platforms=["weibo", "bilibili"])
        )
        await social.persist_batch(case_id=case.id, posts=_POSTS)
        debate = await service.create_debate(case.id, None)

        await service.advance(debate.id)  # R1
        await service.advance(debate.id)  # R2（不应重复生成 R1）
        messages = await repository.list_debate_messages(debate.id)
        assert len([m for m in messages if m.round == 1]) == 2
        assert len([m for m in messages if m.round == 2]) == 2
        # 4 次 LLM 调用 = R1×2 + R2×2（无重复）
        assert len(gateway.calls) == 4
    finally:
        await database.dispose()


async def test_completed_debate_rejects_advance(tmp_path: Path) -> None:
    database, repository, social, gateway, service = await _setup(tmp_path)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="结束保护", platforms=["weibo"])
        )
        await social.persist_batch(case_id=case.id, posts=[_POSTS[0]])
        debate = await service.create_debate(case.id, None)
        for _ in range(4):
            debate = await service.advance(debate.id)
        assert debate.status == "completed"
        with pytest.raises(Exception):  # noqa: B017
            await service.advance(debate.id)
    finally:
        await database.dispose()


async def test_delete_case_cleans_debates(tmp_path: Path) -> None:
    database, repository, social, gateway, service = await _setup(tmp_path)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="删除清理", platforms=["weibo"])
        )
        await social.persist_batch(case_id=case.id, posts=[_POSTS[0]])
        debate = await service.create_debate(case.id, None)
        await service.advance(debate.id)  # 产生消息
        await repository.delete_case(case.id)
        # 删除后 case 域查询 404（case 已不存在）；辩论消息表应无残留。
        with pytest.raises(Exception):  # noqa: B017
            await repository.list_debates(case.id)
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "debate.db"))
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM debate_messages WHERE debate_id = ?",
                (debate.id,),
            ).fetchone()
            assert rows[0] == 0
        finally:
            conn.close()
    finally:
        await database.dispose()


async def test_platform_without_posts_declares_instead_of_speaking(
    tmp_path: Path,
) -> None:
    """缺数据平台不调 LLM 编观点：落「数据缺失」声明且不投票。"""
    from app.core.errors import ApplicationError  # noqa: F401  (保持就近引用)

    database, repository, social, gateway, service = await _setup(tmp_path)
    try:
        case = await repository.create_case(
            CreateCaseRequest(
                topic="三平台辩论", platforms=["weibo", "bilibili", "zhihu"]
            )
        )
        # 仅微博 / 哔哩哔哩有采集数据，知乎为空。
        await social.persist_batch(case_id=case.id, posts=_POSTS)

        debate = await service.create_debate(case.id, None)
        await service.advance(debate.id)  # R1

        messages = await repository.list_debate_messages(debate.id)
        zhihu = [m for m in messages if m.platform == "zhihu"]
        assert len(zhihu) == 1
        assert "【数据缺失】" in zhihu[0].content
        assert "知乎" in zhihu[0].content

        # 推进到 R3 投票：知乎不产生投票记录。
        await service.advance(debate.id)  # R2
        await service.advance(debate.id)  # R3
        votes = await repository.list_debate_votes(debate.id)
        assert all(v.platform != "zhihu" for v in votes)
    finally:
        await database.dispose()


async def test_create_debate_requires_collected_posts(tmp_path: Path) -> None:
    """无任何入库帖子时禁止发起辩论（辩论必须以采集数据为依据）。"""
    from app.core.errors import ApplicationError

    database, repository, social, gateway, service = await _setup(tmp_path)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="空数据辩论", platforms=["weibo"])
        )
        with pytest.raises(ApplicationError) as exc:
            await service.create_debate(case.id, None)
        assert exc.value.code == "debate_no_data"
    finally:
        await database.dispose()
