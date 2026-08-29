"""M3: Collection Definition service 状态机与版本管理。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application.collection_service import CollectionDefinitionService
from app.core.errors import ApplicationError
from app.infrastructure.database import Database
from app.schemas.cases import CreateCaseRequest
from app.schemas.collections import CollectionDefinitionResponse


def _database(tmp_path: Path) -> Database:
    return Database(f"sqlite+aiosqlite:///{tmp_path / 'collection.db'}")


async def _seed_case(database: Database) -> str:
    from app.application.repositories import ApplicationRepository

    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="新能源汽车争议", platforms=["weibo", "zhihu"])
    )
    return case.id


async def test_create_assigns_increasing_versions(tmp_path: Path) -> None:
    database = _database(tmp_path)
    await database.create_schema()
    case_id = await _seed_case(database)
    service = CollectionDefinitionService(database)

    first = await service.create_manual(
        case_id,
        goal="跟踪召回争议",
        platforms=["weibo"],
        platform_queries={"weibo": ["召回", "  ", "召回", "自燃"]},
        exclusions=["广告", "广告"],
    )
    second = await service.create_manual(
        case_id,
        goal="扩展知乎讨论",
        platforms=["weibo", "zhihu"],
    )

    assert first.version == 1
    assert second.version == 2
    # 去空/去重生效
    assert first.platform_queries == {"weibo": ["召回", "自燃"]}
    assert first.exclusions == ["广告"]
    assert first.status == "draft"

    listed = await service.list_for_case(case_id)
    assert [item.version for item in listed] == [2, 1]


async def test_activate_supersedes_old_active_and_rejects_non_draft(tmp_path: Path) -> None:
    database = _database(tmp_path)
    await database.create_schema()
    case_id = await _seed_case(database)
    service = CollectionDefinitionService(database)

    v1 = await service.create_manual(case_id, goal="v1", platforms=["weibo"])
    v2 = await service.create_manual(case_id, goal="v2", platforms=["weibo", "zhihu"])

    active = await service.activate(case_id, v1.id)
    assert active.status == "active"
    assert (await service.get_active(case_id)).id == v1.id

    active2 = await service.activate(case_id, v2.id)
    assert active2.version == 2
    listed = {item.version: item.status for item in await service.list_for_case(case_id)}
    assert listed == {1: "superseded", 2: "active"}

    # 非 draft 不能再次激活
    with pytest.raises(ApplicationError) as exc:
        await service.activate(case_id, v2.id)
    assert exc.value.code == "collection_not_draft"


async def test_cross_case_scope_rejected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    await database.create_schema()
    case_id = await _seed_case(database)
    from app.application.repositories import ApplicationRepository

    other_case = await ApplicationRepository(database).create_case(
        CreateCaseRequest(topic="另一个案例", platforms=["weibo"])
    )
    service = CollectionDefinitionService(database)
    definition = await service.create_manual(case_id, goal="跨案例", platforms=["weibo"])

    with pytest.raises(ApplicationError) as exc:
        await service.activate(other_case.id, definition.id)
    assert exc.value.code == "collection_scope_mismatch"

    with pytest.raises(ApplicationError) as exc2:
        await service.get_for_case(other_case.id, definition.id)
    assert exc2.value.code == "collection_scope_mismatch"


async def test_platforms_must_be_case_subset(tmp_path: Path) -> None:
    database = _database(tmp_path)
    await database.create_schema()
    case_id = await _seed_case(database)
    service = CollectionDefinitionService(database)

    with pytest.raises(ApplicationError) as exc:
        await service.create_manual(
            case_id, goal="非法平台", platforms=["bilibili"]
        )
    assert exc.value.code == "collection_validation_failed"


async def test_generate_falls_back_to_topic_and_stays_draft(tmp_path: Path) -> None:
    database = _database(tmp_path)
    await database.create_schema()
    case_id = await _seed_case(database)
    # llm=None：generate_platform_keywords 直接回退每平台 [topic]
    service = CollectionDefinitionService(database, llm=None)

    record = await service.generate(case_id)
    assert record.status == "draft"
    assert record.filters.get("generated_by") == "fallback"
    assert record.platform_queries == {"weibo": ["新能源汽车争议"], "zhihu": ["新能源汽车争议"]}

    response = CollectionDefinitionResponse.from_record(record)
    assert response.status == "draft"


async def test_revise_creates_new_draft_without_touching_history(tmp_path: Path) -> None:
    database = _database(tmp_path)
    await database.create_schema()
    case_id = await _seed_case(database)
    service = CollectionDefinitionService(database)

    v1 = await service.create_manual(
        case_id, goal="初版", platforms=["weibo"], platform_queries={"weibo": ["召回"]}
    )
    v2 = await service.revise(
        case_id,
        v1.id,
        goal="修订版",
        platforms=["weibo", "zhihu"],
        platform_queries={"weibo": ["自燃"], "zhihu": ["讨论"]},
    )

    assert v2.version == 2
    assert v2.goal == "修订版"
    assert v2.platform_queries == {"weibo": ["自燃"], "zhihu": ["讨论"]}
    previous = await service.get_for_case(case_id, v1.id)
    assert previous.goal == "初版"
    assert previous.platform_queries == {"weibo": ["召回"]}


async def test_keywords_for_intersects_and_falls_back_per_platform(tmp_path: Path) -> None:
    database = _database(tmp_path)
    await database.create_schema()
    case_id = await _seed_case(database)
    service = CollectionDefinitionService(database)

    v1 = await service.create_manual(
        case_id,
        goal="关键词投影",
        platforms=["weibo"],
        platform_queries={"weibo": ["召回"]},
    )
    await service.activate(case_id, v1.id)
    active = await service.get_active(case_id)
    assert active is not None

    keywords = service.keywords_for(
        active,
        requested_platforms=["weibo", "zhihu"],
        fallback_topic="新能源汽车争议",
    )
    # 定义覆盖 weibo；zhihu 不在定义中 → 回退 topic，不静默丢平台
    assert keywords == {
        "weibo": ["召回"],
        "zhihu": ["新能源汽车争议"],
    }
