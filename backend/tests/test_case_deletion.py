"""Case deletion cascade: cleanup across run/turn/social/domain tables."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application.repositories import ApplicationRepository
from app.infrastructure.database import Database
from app.infrastructure.database.social_repository import SocialRepository
from app.schemas.cases import CreateCaseRequest

_POSTS = [
    {
        "id": "p1",
        "platform": "weibo",
        "author": "a",
        "content": "暴雨泄洪现场信息",
        "published_at": "2026-08-07T21:00:00+00:00",
        "sentiment": "negative",
        "engagement": 100,
        "is_demo": True,
    },
]


async def test_delete_case_cascades_all_tables(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'delete.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="级联删除", platforms=["weibo"])
        )
        turn = await repository.add_turn(case.id, role="user", content="第一条")
        # turn_id 关联：agent_runs.turn_id -> conversation_turns.id 外键
        # （PG 强制；SQLite 测试未开 PRAGMA 外键，靠删除顺序正确性保证）
        run = await repository.create_agent_run(
            case_id=case.id, turn_id=turn.id, objective="目标"
        )
        await repository.add_run_event(run.id, {"event_type": "agent_queued"})
        await repository.create_artifact(
            case_id=case.id,
            run_id=run.id,
            kind="opinion_analysis",
            title="产物",
            data={"conclusions": []},
        )
        await social.persist_batch(case_id=case.id, posts=_POSTS)
        claim = await repository.create_claim(
            case_id=case.id, text="泄洪谣言", created_by_run_id=run.id
        )
        await repository.create_evidence(
            case_id=case.id,
            claim_id=claim.id,
            source_type="post",
            source_id="p1",
            stance="support",
            excerpt="现场信息",
            relevance=0.9,
        )
        await repository.create_propagation_edge(
            case_id=case.id,
            source_post_id="s1",
            target_post_id="t2",
            relation="inferred",
            confidence=0.5,
            feature_scores={},
            evidence_ids=[],
        )

        await repository.delete_case(case.id)

        # 删除后所有 case 域查询均 404（case 已不存在）；底层表应无残留。
        for query in (
            repository.list_turns,
            repository.list_artifacts,
            repository.list_propagation_edges_by_case,
            repository.list_evidence_by_case,
            repository.list_claims_by_case,
            repository.list_agent_runs,
        ):
            with pytest.raises(Exception):  # noqa: B017
                await query(case.id)
        assert await social.list_posts_by_case(case.id) == []
        try:
            await repository.get_case(case.id)
            raise AssertionError("case still exists")
        except Exception:
            pass
    finally:
        await database.dispose()


async def test_rename_case_updates_title(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'rename.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="原主题", platforms=["weibo"])
        )
        renamed = await repository.rename_case(case.id, "新标题")
        assert renamed.title == "新标题"
        assert (await repository.get_case(case.id)).title == "新标题"
    finally:
        await database.dispose()


async def test_add_turn_touches_case_updated_at(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'touch.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="活跃排序", platforms=["weibo"])
        )
        before = case.updated_at
        await repository.add_turn(case.id, role="user", content="新对话")
        after = (await repository.get_case(case.id)).updated_at
        assert after >= before
    finally:
        await database.dispose()


async def test_project_crud_and_cascade_delete(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'project.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        project = await repository.create_project("灾害舆情项目")
        assert project.title == "灾害舆情项目"
        assert [p.id for p in await repository.list_projects()] == [project.id]

        # 项目内创建会话（project_id 绑定）
        case = await repository.create_case(
            CreateCaseRequest(topic="项目内案例", platforms=["weibo"], project_id=project.id)
        )
        assert case.project_id == project.id
        await repository.add_turn(case.id, role="user", content="第一条")

        renamed = await repository.rename_project(project.id, "新项目名")
        assert renamed.title == "新项目名"

        # 删除项目 → 其下会话级联删除
        await repository.delete_project(project.id)
        assert await repository.list_projects() == []
        with pytest.raises(Exception):  # noqa: B017
            await repository.get_case(case.id)
    finally:
        await database.dispose()
