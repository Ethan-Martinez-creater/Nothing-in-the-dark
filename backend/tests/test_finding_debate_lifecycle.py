"""Second-round fix tests (FINDING_MULTI_AGENT_DEBATE_SECOND_ROUND_FIX_PLAN).

覆盖矩阵 FC2-DEL-01~04 / FC2-IMM-01~03 / FC2-MOD-01~02 /
FC2-SNAP-01~04 / FC2-DATA-01~03 / FC2-PROMPT-01。

注意：本文件为每个 Database 注册 PRAGMA foreign_keys=ON，使 SQLite 真实
强制 FK 约束（与 PostgreSQL 行为一致），从而让 FC2-01 删除顺序回归可测。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.application.debate_service import DebateService
from app.application.finding_service import FindingService
from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.infrastructure.database import Database
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.llm import LLMGateway, LLMMessage, LLMResponse
from app.schemas.cases import CreateCaseRequest

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


class CaptureGateway(LLMGateway):
    """捕获每次调用的 system/user 全文，并按轮次路由回复。"""

    def __init__(self) -> None:
        self.systems: list[str] = []
        self.users: list[str] = []
        self.calls = 0

    @property
    def configured(self) -> bool:
        return True

    async def complete(self, *, messages: list[LLMMessage], route=None, **kw):
        self.calls += 1
        system = messages[0].content
        user = messages[-1].content
        self.systems.append(system)
        self.users.append(user)
        if "主持人" in system or "对抗性审查主持人" in system:
            return LLMResponse(
                message=LLMMessage(
                    role="assistant",
                    content=(
                        "### 共识\n时间线无争议。\n\n"
                        "### 主要反证与冲突\n无。\n\n"
                        "### 证据缺口\n官方通报缺失。\n\n"
                        "### 替代解释\n自然泄洪。\n\n"
                        "### 建议的复核态度\ninsufficient\n理由：证据不足。\n\n"
                        "> 本结果仅用于辅助人工审核，不自动修改 Finding 状态。"
                    ),
                ),
                model="fake",
            )
        if "第 3 轮" in user and "Finding verdict" in user:
            return LLMResponse(
                message=LLMMessage(
                    role="assistant",
                    content=json.dumps(
                        {"choice": "supported", "reason": "证据判断"}
                    ),
                ),
                model="fake",
            )
        if "第 3 轮" in user:
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


class FakeProfiles:
    def __init__(self) -> None:
        self.refresh_calls = 0

    async def get_profile(self, platform: str):
        return SimpleNamespace(content="某平台的长期观察结论，仅作风格参考。")

    async def refresh_from_debate(self, roles, posts, messages, *, topic=""):
        self.refresh_calls += 1


def _enable_fk(database: Database) -> None:
    """SQLite 每连接强制外键，让删除顺序缺陷在测试中真实暴露。"""
    engine = database.engine.sync_engine

    def _set_pragma(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(engine, "connect", _set_pragma)


async def _setup(
    tmp_path: Path,
    *,
    with_posts: bool = True,
    platforms: list[str] | None = None,
    evidence_count: int = 1,
):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'life.db'}")
    _enable_fk(database)
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)
    finding_service = FindingService(database, repository)
    gateway = CaptureGateway()
    profiles = FakeProfiles()
    service = DebateService(
        repository,
        social,
        gateway,
        profiles=profiles,
        finding_service=finding_service,
    )
    case = await repository.create_case(
        CreateCaseRequest(
            topic="辩论测试", platforms=platforms or ["weibo", "bilibili"]
        )
    )
    if with_posts:
        await social.persist_batch(case_id=case.id, posts=_POSTS)
    evidence_ids = []
    for index in range(evidence_count):
        evidence = await repository.create_evidence(
            case_id=case.id,
            source_type="post",
            source_id=f"s{index}",
            stance="supports",
            excerpt=f"证据正文第 {index} 条：泄洪现场信息片段。",
        )
        evidence_ids.append(evidence.id)
    finding = await finding_service.create_manual(
        case.id,
        kind="manual",
        title="泄洪致灾结论",
        statement="本次灾害主要由泄洪导致。",
        confidence=0.83,
        source_type="post",
        source_id="p1",
        evidence_links=[(eid, "supports") for eid in evidence_ids],
    )
    return database, repository, service, gateway, profiles, finding, case


async def _run_to_completed(repository, service, debate) -> None:
    for _ in range(4):
        debate = await service.advance(debate.id)
    assert debate.status == "completed"


async def _count(extra, repository, model, **kwargs) -> int:
    from sqlalchemy import func, select

    where = [getattr(model, key) == value for key, value in kwargs.items()]
    async with repository._database.session_factory() as session:
        return await session.scalar(select(func.count()).select_from(model).where(*where))


# ---------------- FC2-DEL：删除生命周期 ----------------

async def test_fc2_del_01_delete_case_with_active_challenge(tmp_path: Path) -> None:
    """Case + in_progress Challenge 删除成功，无 FK violation。"""
    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    assert debate.status == "in_progress"

    await repository.delete_case(case.id)

    with pytest.raises(ApplicationError):
        await repository.get_case(case.id)
    from app.infrastructure.database.models import (
        DebateMessageRecord,
        DebateRecord,
        DebateVoteRecord,
    )

    assert await _count(None, repository, DebateRecord) == 0
    assert await _count(None, repository, DebateMessageRecord) == 0
    assert await _count(None, repository, DebateVoteRecord) == 0


async def test_fc2_del_02_delete_case_with_completed_challenge(
    tmp_path: Path,
) -> None:
    """completed Challenge 后删除 Case 成功。"""
    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    await _run_to_completed(repository, service, debate)

    await repository.delete_case(case.id)

    from app.infrastructure.database.models import DebateRecord

    assert await _count(None, repository, DebateRecord) == 0


async def test_fc2_del_03_delete_project_with_challenge_case(
    tmp_path: Path,
) -> None:
    """Project 含 Challenge Case + 普通 Case，删除全部成功。"""
    _, repository, service, _, _, finding, _ = await _setup(tmp_path)
    project = await repository.create_project("二轮修复项目")
    case_a = await repository.create_case(
        CreateCaseRequest(
            topic="含挑战案件", platforms=["weibo", "bilibili"], project_id=project.id
        )
    )
    await repository.get_case(case_a.id)
    await repository.create_evidence(
        case_id=case_a.id, source_type="post", source_id="x", stance="supports",
        excerpt="A 案证据",
    )
    await repository.create_evidence(
        case_id=case_a.id, source_type="post", source_id="y", stance="supports",
        excerpt="A 案证据二",
    )
    case_a_row = await repository.get_case(case_a.id)
    finding_a = await finding_service_case(repository, case_a_row)
    # case_a 需要有帖子才能创建 challenge
    from app.infrastructure.database.social_repository import SocialRepository

    social = SocialRepository(repository._database)
    await social.persist_batch(case_id=case_a.id, posts=_POSTS)
    debate, _ = await service.create_finding_challenge(case_a.id, finding_a.id)
    assert debate is not None

    case_b = await repository.create_case(
        CreateCaseRequest(topic="普通案件", platforms=["weibo"], project_id=project.id)
    )

    await repository.delete_project(project.id)

    with pytest.raises(ApplicationError):
        await repository.get_case(case_a.id)
    with pytest.raises(ApplicationError):
        await repository.get_case(case_b.id)
    from app.infrastructure.database.models import (
        DebateMessageRecord,
        DebateRecord,
        DebateVoteRecord,
    )

    assert await _count(None, repository, DebateRecord) == 0
    assert await _count(None, repository, DebateMessageRecord) == 0
    assert await _count(None, repository, DebateVoteRecord) == 0


async def finding_service_case(repository, case_row):
    from app.application.finding_service import FindingService

    fs = FindingService(repository._database, repository)
    evidence_ids = [
        e.id
        for e in await repository.list_evidence_by_case(case_row.id, limit=10)
    ]
    return await fs.create_manual(
        case_row.id,
        kind="manual",
        title="A 案结论",
        statement="A 案结论陈述。",
        evidence_links=[(eid, "supports") for eid in evidence_ids],
    )


async def test_fc2_del_04_mixed_legacy_and_challenge_delete(
    tmp_path: Path,
) -> None:
    """同一 Case 的 legacy case_debate + finding_challenge 全部清理。"""
    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    legacy = await service.create_debate(case.id, "全案辩论")
    challenge, _ = await service.create_finding_challenge(case.id, finding.id)
    assert legacy is not None and challenge is not None

    await repository.delete_case(case.id)

    from app.infrastructure.database.models import (
        DebateMessageRecord,
        DebateRecord,
        DebateVoteRecord,
    )

    assert await _count(None, repository, DebateRecord) == 0
    assert await _count(None, repository, DebateMessageRecord) == 0
    assert await _count(None, repository, DebateVoteRecord) == 0


# ---------------- FC2-IMM：completed 不可变 ----------------

async def test_fc2_imm_01_completed_challenge_rejects_user_message(
    tmp_path: Path,
) -> None:
    """completed finding_challenge 拒绝插话，错误码 debate_completed。"""
    from app.infrastructure.database.models import DebateMessageRecord as DMR

    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    await _run_to_completed(repository, service, debate)

    before = await _count(None, repository, DMR)
    with pytest.raises(ApplicationError) as exc:
        await service.add_user_message(debate.id, "还能说话吗")
    assert exc.value.code == "debate_completed"
    after = await _count(None, repository, DMR)
    assert after == before


async def test_fc2_imm_02_completed_legacy_rejects_user_message(
    tmp_path: Path,
) -> None:
    """completed legacy case_debate 同样拒绝。"""
    _, repository, service, _, _, _, case = await _setup(tmp_path)
    debate = await service.create_debate(case.id, "全案辩论")
    await _run_to_completed(repository, service, debate)

    with pytest.raises(ApplicationError) as exc:
        await service.add_user_message(debate.id, "还能说话吗")
    assert exc.value.code == "debate_completed"


async def test_fc2_imm_03_in_progress_still_allows_interjection(
    tmp_path: Path,
) -> None:
    """in_progress 仍可正常插话。"""
    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    message = await service.add_user_message(debate.id, "请检查相关性混淆")
    assert message.role == "user"
    assert message.debate_id == debate.id


# ---------------- FC2-MOD：R4 Moderator snapshot ----------------

async def test_fc2_mod_01_r4_prompt_has_snapshot_and_history(
    tmp_path: Path,
) -> None:
    """R4 的 system 含原 Finding + Evidence；user 含 R1/R2 发言与 R3 verdict。"""
    _, repository, service, gateway, _, finding, case = await _setup(
        tmp_path, evidence_count=2
    )
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    await _run_to_completed(repository, service, debate)

    r4_system = gateway.systems[-1]
    r4_user = gateway.users[-1]
    messages = await repository.list_debate_messages(debate.id)
    votes = await repository.list_debate_votes(debate.id)

    assert finding.statement in r4_system
    assert "证据正文第 0 条" in r4_system
    assert "【原始 Finding】" in r4_system
    assert "【创建 Challenge 时关联 Evidence】" in r4_system
    # 约束文案
    assert "Evidence 是可引用的事实依据" in r4_system
    assert "不得自动宣告 Finding verified/rejected" in r4_system
    # history + verdict
    first_round = next(m for m in messages if m.round == 1 and m.role == "platform_role")
    assert first_round.content[:20] in r4_user
    assert "第3轮投票" in r4_user or "判定" in r4_user
    assert len(votes) == 2


async def test_fc2_mod_02_r4_legacy_prompt_unaffected(tmp_path: Path) -> None:
    """legacy R4 prompt 不含 Finding block。"""
    _, repository, service, gateway, _, _, case = await _setup(tmp_path)
    debate = await service.create_debate(case.id, "全案辩论")
    await _run_to_completed(repository, service, debate)

    r4_system = gateway.systems[-1]
    assert "【原始 Finding】" not in r4_system
    assert "证据正文" not in r4_system
    assert "对抗性审查主持人" not in r4_system


# ---------------- FC2-SNAP：Evidence snapshot 完整性 ----------------

async def test_fc2_snap_01_snapshot_keeps_all_evidence_refs(
    tmp_path: Path,
) -> None:
    """15 条 Evidence → snapshot 保存全部 15 条 ref。"""
    _, repository, service, _, _, finding, case = await _setup(
        tmp_path, evidence_count=15
    )
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    snapshot = debate.context_snapshot
    assert len(snapshot["evidence"]) == 15
    refs = {e["evidence_ref"] for e in snapshot["evidence"]}
    assert len(refs) == 15


async def test_fc2_snap_02_excerpt_at_most_12(tmp_path: Path) -> None:
    """excerpt 只出现在前 12 条。"""
    _, repository, service, _, _, finding, case = await _setup(
        tmp_path, evidence_count=15
    )
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    snapshot = debate.context_snapshot
    with_excerpt = [e for e in snapshot["evidence"] if "excerpt" in e]
    assert len(with_excerpt) == 12
    # 13+ 条只有 ref/relation
    assert "excerpt" not in snapshot["evidence"][12]


async def test_fc2_snap_03_llm_prompt_evidence_at_most_12(
    tmp_path: Path,
) -> None:
    """R1 prompt 注入的 Evidence 不超过 12 条。"""
    _, repository, service, gateway, _, finding, case = await _setup(
        tmp_path, evidence_count=15
    )
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    await service.advance(debate.id)

    r1_system = gateway.systems[0]
    ref_count = r1_system.count("ref=")
    assert 12 >= ref_count > 0
    # 前 12 条有摘录，13+ 不注入
    assert "证据正文第 14 条" not in r1_system


async def test_fc2_snap_04_snapshot_immutable_after_finding_changes(
    tmp_path: Path,
) -> None:
    """创建后修改 Finding / Evidence links，snapshot 不变。"""
    _, repository, service, _, _, finding, case = await _setup(tmp_path)
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    original = dict(debate.context_snapshot)

    await service._finding_service._findings.update_status(
        finding.id, "under_review"
    )
    new_evidence = await repository.create_evidence(
        case_id=case.id,
        source_type="post",
        source_id="new1",
        stance="supports",
        excerpt="新证据",
    )
    await service._finding_service.add_evidence_link(
        case.id, finding.id, new_evidence.id, "supports"
    )

    reloaded = await repository.get_debate(debate.id)
    assert reloaded.context_snapshot == original


# ---------------- FC2-DATA / FC2-PROMPT：创建条件与 Profile ----------------

async def test_fc2_data_01_no_posts_rejects_challenge(tmp_path: Path) -> None:
    """全 Case 无帖子 → debate_no_data。"""
    _, repository, service, _, _, finding, case = await _setup(
        tmp_path, with_posts=False
    )
    with pytest.raises(ApplicationError) as exc:
        await service.create_finding_challenge(case.id, finding.id)
    assert exc.value.code == "debate_no_data"
    assert len(await repository.list_debates(case.id)) == 0


async def test_fc2_data_02_partial_platform_data_allows_creation(
    tmp_path: Path,
) -> None:
    """仅 weibo 有数据 → 仍可创建（zhihu 运行时 fail-closed）。"""
    _, repository, service, gateway, _, finding, case = await _setup(
        tmp_path, platforms=["weibo", "zhihu"]
    )
    debate, created = await service.create_finding_challenge(case.id, finding.id)
    assert created is True
    # 推进三轮：zhihu 全程不调 LLM（每轮仅 weibo 1 次 = 3 次）
    for _ in range(3):
        debate = await service.advance(debate.id)
    assert gateway.calls == 3
    votes = await repository.list_debate_votes(debate.id)
    assert {v.platform for v in votes} == {"weibo"}
    messages = await repository.list_debate_messages(debate.id)
    missing = [m for m in messages if m.platform == "zhihu" and m.role == "platform_role"]
    assert missing and "【数据缺失】" in missing[0].content


async def test_fc2_data_03_no_data_platform_fail_closed(tmp_path: Path) -> None:
    """无数据平台：R1/R2/R3 不调用 LLM、不产生 Vote。"""
    _, repository, service, gateway, _, finding, case = await _setup(
        tmp_path, platforms=["bilibili", "zhihu"]
    )
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    calls_before = gateway.calls
    for _ in range(3):
        debate = await service.advance(debate.id)
    # bilibili 每轮 1 次 = 3 次；zhihu 0 次
    assert gateway.calls - calls_before == 3
    votes = await repository.list_debate_votes(debate.id)
    assert {v.platform for v in votes} == {"bilibili"}


async def test_fc2_prompt_01_profile_not_fact_source(tmp_path: Path) -> None:
    """challenge system prompt 含帖子 + Evidence + 画像非事实证据文案。"""
    _, repository, service, gateway, profiles, finding, case = await _setup(
        tmp_path, evidence_count=2
    )
    assert profiles is not None
    debate, _ = await service.create_finding_challenge(case.id, finding.id)
    await service.advance(debate.id)

    r1_system = gateway.systems[0]
    assert "该平台采集到的帖子" in r1_system or "采集的帖子" in r1_system
    assert "已关联 Evidence" in r1_system
    assert "它本身不是事实证据" in r1_system
    assert "不是事实证据" in r1_system
    assert "1. 本次该平台采集到的帖子；2. 当前 Finding 已关联 Evidence" in r1_system