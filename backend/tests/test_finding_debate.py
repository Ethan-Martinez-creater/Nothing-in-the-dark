"""Finding-level adversarial challenge: persistence, four-round semantics, boundaries.

覆盖计划文档 M7 后端清单：
legacy 兼容 / challenge 持久化 / case scope / create-or-resume /
completed 后新建 / R3 verdict 校验 / fail-safe / 无数据平台 fail-closed /
Finding status 不变 / snapshot 固定 / profile 画像隔离。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application.debate_service import DebateService
from app.application.finding_service import FindingService
from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.infrastructure.database import Database
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse
from app.schemas.cases import CreateCaseRequest


class ChallengeGateway(LLMGateway):
    """按模式与轮次路由回复：challenge R3 输出 verdict JSON。"""

    def __init__(self, *, verdict: str = "supported") -> None:
        self.calls = 0
        self.verdict = verdict

    @property
    def configured(self) -> bool:
        return True

    async def complete(self, *, messages: list[LLMMessage], route=None, **kw):
        self.calls += 1
        system = messages[0].content
        user = messages[-1].content
        if "主持人" in system or "对抗性审查主持人" in system:
            return LLMResponse(
                message=LLMMessage(
                    role="assistant",
                    content=(
                        "### 共识\n双方认可时间线。\n\n"
                        "### 主要反证与冲突\nbilibili 辟谣视频。\n\n"
                        "### 证据缺口\n缺官方通报。\n\n"
                        "### 替代解释\n自然泄洪。\n\n"
                        "### 建议的复核态度\ninsufficient\n理由：证据不足。\n\n"
                        "> 本结果仅用于辅助人工审核，不自动修改 Finding 状态。"
                    ),
                ),
                model="fake",
            )
        if "Finding verdict 投票" in user:
            return LLMResponse(
                message=LLMMessage(
                    role="assistant",
                    content=json.dumps(
                        {"choice": self.verdict, "reason": "证据判断"}
                    ),
                ),
                model="fake",
            )
        if "观点投票" in user:
            return LLMResponse(
                message=LLMMessage(
                    role="assistant",
                    content=json.dumps(
                        {"choice": "weibo", "reason": "微博有首发信息"}
                    ),
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


class FakeProfiles:
    """记录 refresh_from_debate 调用，验证画像隔离。"""

    def __init__(self) -> None:
        self.refresh_calls: list[str] = []

    async def get_profile(self, platform: str):
        return None

    async def refresh_from_debate(self, roles, posts, messages, *, topic=""):
        self.refresh_calls.append(topic)
        return SimpleNamespace(platform=roles, content="updated")


async def _setup(
    tmp_path: Path, *, verdict: str = "supported"
):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fdebate.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)
    finding_service = FindingService(database, repository)
    gateway = ChallengeGateway(verdict=verdict)
    profiles = FakeProfiles()
    service = DebateService(
        repository,
        social,
        gateway,
        profiles=profiles,
        finding_service=finding_service,
    )
    case = await repository.create_case(
        CreateCaseRequest(topic="辩论测试", platforms=["weibo", "bilibili"])
    )
    await social.persist_batch(case_id=case.id, posts=_POSTS)
    evidence = await repository.create_evidence(
        case_id=case.id,
        source_type="post",
        source_id="p1",
        stance="supports",
        excerpt="微博现场帖子：暴雨泄洪现场信息，等待官方说明。",
    )
    finding = await finding_service.create_manual(
        case.id,
        kind="manual",
        title="泄洪致灾结论",
        statement="本次灾害主要由泄洪导致。",
        confidence=0.83,
        source_type="post",
        source_id="p1",
        evidence_links=[(evidence.id, "supports")],
    )
    return database, repository, service, gateway, profiles, finding, case


async def _run_to_completed(repository, service, debate) -> None:
    for _ in range(4):
        debate = await service.advance(debate.id)
    assert debate.status == "completed"


async def test_legacy_case_debate_defaults_mode(tmp_path: Path) -> None:
    """1. legacy Debate 不回归：默认 mode=case_debate。"""
    _, repository, service, _, _, _, case = await _setup(tmp_path)
    debate = await service.create_debate(case.id, "全案辩论")
    assert debate.mode == "case_debate"
    assert debate.finding_id is None
    assert debate.context_snapshot == {}
    listed = await repository.list_debates(case.id)
    assert [d.mode for d in listed] == ["case_debate"]


async def test_create_finding_challenge_persists_finding_id_and_snapshot(
    tmp_path: Path,
) -> None:
    """2. 创建 Finding Challenge：mode/finding_id/snapshot 持久化。"""
    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    debate, created = await service.create_finding_challenge(
        case.id, finding.id
    )
    assert created is True
    assert debate.mode == "finding_challenge"
    assert debate.finding_id == finding.id
    snapshot = debate.context_snapshot
    assert snapshot["prompt_version"] == "finding_challenge_v1"
    assert snapshot["finding"]["id"] == finding.id
    assert snapshot["finding"]["statement"] == finding.statement
    assert snapshot["finding"]["confidence"] == pytest.approx(0.83)
    assert len(snapshot["evidence"]) == 1
    assert snapshot["evidence"][0]["relation"] == "supports"
    assert "微博现场帖子" in snapshot["evidence"][0]["excerpt"]
    assert snapshot["sources"][0]["source_type"] == "post"
    history = await repository.list_debates_for_finding(case.id, finding.id)
    assert [d.id for d in history] == [debate.id]


async def test_challenge_rejects_cross_case_finding(tmp_path: Path) -> None:
    """3. Case scope 校验：跨 Case Finding 绑定失败。"""
    _, _, service, _, _, finding, case = await _setup(tmp_path)
    other_case = await service._repository.create_case(
        CreateCaseRequest(topic="另一个案件", platforms=["weibo"])
    )
    with pytest.raises(ApplicationError) as exc:
        await service.create_finding_challenge(other_case.id, finding.id)
    assert exc.value.code == "finding_scope_mismatch"


async def test_create_or_resume_returns_active(tmp_path: Path) -> None:
    """4. create-or-resume：同一 Finding 只有一个进行中 Challenge。"""
    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    first, created1 = await service.create_finding_challenge(case.id, finding.id)
    second, created2 = await service.create_finding_challenge(case.id, finding.id)
    assert created1 is True and created2 is False
    assert second.id == first.id
    active = await repository.get_active_debate_for_finding(case.id, finding.id)
    assert active is not None and active.id == first.id
    debates = await repository.list_debates(case.id)
    assert len(debates) == 1


async def test_new_challenge_after_completion(tmp_path: Path) -> None:
    """5. completed 后可创建新 Challenge。"""
    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    first, _ = await service.create_finding_challenge(case.id, finding.id)
    await _run_to_completed(repository, service, first)
    second, created = await service.create_finding_challenge(case.id, finding.id)
    assert created is True
    assert second.id != first.id
    assert second.status == "in_progress"
    history = await repository.list_debates_for_finding(case.id, finding.id)
    assert len(history) == 2


async def test_challenge_r3_valid_verdict(tmp_path: Path) -> None:
    """6. R3 合法 verdict：choice 落库且不投给平台。"""
    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    for _ in range(3):
        debate = await service.advance(debate.id)
    votes = await repository.list_debate_votes(debate.id)
    assert len(votes) == 2
    assert {v.choice for v in votes} == {"supported"}
    assert all(v.choice not in ("weibo", "bilibili") for v in votes)
    assert all(v.reason for v in votes)


async def test_challenge_r3_invalid_verdict_failsafe(tmp_path: Path) -> None:
    """7. R3 非法 verdict fail-safe：保守归为 insufficient。"""
    _, repository, service, _, _, finding, case = await _setup(
        tmp_path, verdict="refuted"  # 先用合法值建 case，gateway 换非法值
    )
    service._llm.verdict = "totally_wrong_choice"
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    for _ in range(3):
        debate = await service.advance(debate.id)
    votes = await repository.list_debate_votes(debate.id)
    assert len(votes) == 2
    assert {v.choice for v in votes} == {"insufficient"}
    assert all("非法 verdict" in v.reason or "保守归为" in v.reason for v in votes)


async def test_challenge_platform_without_posts_failclosed(
    tmp_path: Path,
) -> None:
    """8. 无数据平台 fail-closed：不调用 LLM、落【数据缺失】、R3 不投票。"""
    _, repository, service, gateway, _, finding, case = await _setup(tmp_path)
    # 追加一个无数据平台并重建 challenge（platform_roles 来自 case.platforms）
    case_row = await repository.get_case(case.id)
    case_row.platforms = ["weibo", "bilibili", "tieba"]
    async with database_session(repository) as session:
        await session.merge(case_row)
        await session.commit()
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    for _ in range(3):
        debate = await service.advance(debate.id)
    votes = await repository.list_debate_votes(debate.id)
    assert {v.platform for v in votes} == {"weibo", "bilibili"}
    messages = await repository.list_debate_messages(debate.id)
    missing = [
        m for m in messages if m.platform == "tieba" and m.role == "platform_role"
    ]
    assert missing and "【数据缺失】" in missing[0].content


def database_session(repository):
    return repository._database.session_factory()


async def test_finding_status_unchanged_by_debate(tmp_path: Path) -> None:
    """9. Debate 全程不修改 Finding status（含 completed）。"""
    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    await _run_to_completed(repository, service, debate)
    stored = await service._finding_service.get_for_case(case.id, finding.id)
    assert stored.status == "candidate"
    # verified 后仍允许再次 challenge，且不翻转 verified
    await service._finding_service._findings.update_status(
        finding.id, "verified"
    )
    again, created = await service.create_finding_challenge(case.id, finding.id)
    assert created is True
    for _ in range(4):
        again = await service.advance(again.id)
    stored = await service._finding_service.get_for_case(case.id, finding.id)
    assert stored.status == "verified"


async def test_snapshot_immutable_after_creation(tmp_path: Path) -> None:
    """10. snapshot 创建后固定，不随 Finding 后续修改变化。"""
    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    original = dict(debate.context_snapshot)
    await service._finding_service._findings.update_status(
        finding.id, "under_review"
    )
    await service.add_user_message(
        debate.id, "请重点检查这个结论是否把相关性错误解释为因果关系。"
    )
    reloaded = await repository.get_debate(debate.id)
    assert reloaded.context_snapshot == original
    assert reloaded.context_snapshot["finding"]["status"] == "candidate"


async def test_case_debate_profile_refresh_still_works(tmp_path: Path) -> None:
    """11. case_debate completed → profile refresh 不回归。"""
    _, repository, service, _, profiles, _, case = await _setup(tmp_path)
    debate = await service.create_debate(case.id, "全案辩论")
    await _run_to_completed(repository, service, debate)
    assert len(profiles.refresh_calls) == 1


async def test_challenge_does_not_refresh_profile(tmp_path: Path) -> None:
    """12. finding_challenge completed → 不污染平台画像。"""
    _, repository, service, _, profiles, finding, case = await _setup(tmp_path)
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    await _run_to_completed(repository, service, debate)
    assert profiles.refresh_calls == []


async def test_challenge_moderator_summary_structure(tmp_path: Path) -> None:
    """补充：R4 主持人综合落盘为 moderator 消息（结构由 prompt 固定）。"""
    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    await _run_to_completed(repository, service, debate)
    messages = await repository.list_debate_messages(debate.id)
    moderator = [m for m in messages if m.role == "moderator"]
    assert len(moderator) == 1 and moderator[0].round == 4
    assert "### 共识" in moderator[0].content
    assert "不自动修改 Finding 状态" in moderator[0].content
