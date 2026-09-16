"""fixture seed 的契约测试：冻结数据能落到真实表结构并被生产查询读到。"""

from __future__ import annotations

import pytest

from app.evaluation.agent_dataset import load_suite
from app.evaluation.agent_fixture_seed import build_stack, seed_fixture
from tests.memory_db import MemoryDatabase


@pytest.fixture
async def stack():
    database = MemoryDatabase()
    await database.create_schema()
    try:
        yield build_stack(database)
    finally:
        await database.dispose()


@pytest.fixture(scope="module")
def suite():
    return load_suite()


async def test_seed_grounding_fixture(stack, suite) -> None:
    fixture = suite.fixtures["case_grounding"]
    seeded = await seed_fixture(stack, fixture_id="case_grounding", fixture=fixture)

    case_id = seeded.case_id("grounding")
    case = await stack.repository.get_case(case_id)
    assert case.topic == "青禾乳业酸奶添加剂争议"
    assert set(case.platforms) == {"weibo", "bilibili"}

    posts = await stack.social.list_posts_by_case(case_id)
    assert len(posts) == 10
    assert {post.platform for post in posts} == {"weibo", "bilibili"}
    # author_id 必须从 raw.user_id 正确注入（否则 workspace 统计拿不到作者）
    assert all(post.author_id for post in posts)

    comments = await stack.social.count_comments(case_id)
    assert comments == 3  # gr_p1 两条 + gr_p3 一条

    claims = await stack.repository.list_claims_by_case(case_id)
    assert len(claims) == 3

    evidence = await stack.repository.list_evidence_by_case(case_id)
    assert len(evidence) == 5
    assert {item.stance for item in evidence} == {"supports", "contradicts", "context"}

    findings = await stack.finding_repository.list(case_id)
    assert len(findings) == 2
    by_kind = {item.kind: item for item in findings}
    assert by_kind["verification"].status == "candidate"
    assert by_kind["opinion"].status == "under_review"
    # evidence 链接必须落到 finding_evidence_links
    linked = await stack.finding_repository.list_evidence_links(
        by_kind["verification"].id
    )
    assert len(linked) == 3

    review_items = await stack.repository.list_review_items(case_id)
    assert len(review_items) == 1
    assert review_items[0].object_type == "finding"

    reports = await stack.report_repository.list_for_case(case_id)
    assert len(reports) == 1

    # ref_map 必须覆盖全部引用类型
    for prefix in ("case", "post", "account", "claim", "evidence", "finding", "artifact", "report"):
        assert any(key.startswith(prefix + ":") for key in seeded.ref_map), prefix


async def test_seed_cross_fixture(stack, suite) -> None:
    fixture = suite.fixtures["case_cross"]
    seeded = await seed_fixture(stack, fixture_id="case_cross", fixture=fixture)

    assert sorted(seeded.cases) == ["cross_a", "cross_b", "cross_c"]
    cross_a = seeded.case_id("cross_a")
    cross_b = seeded.case_id("cross_b")

    links = await stack.cross_repository.list_for_case(cross_a)
    assert len(links) >= 1
    assert {link.relation_type for link in links} >= {"shared_actor"}

    entities = await stack.workspace_repository.list_entities_for_case(cross_a)
    assert len(entities) == 1
    assert entities[0].canonical_name == "热点搬运工"

    signals = await stack.signal_repository.list_for_case(cross_a)
    assert len(signals) == 1
    assert signals[0].signal_type == "shared_actor_spread"
    assert signals[0].severity == "high"

    # 跨调查链接的 evidence_refs 必须解析成真实 post id
    assert seeded.ref_map["post:ca_p3"]
    assert seeded.ref_map["post:cb_p1"]


async def test_seed_review_fixture_has_thirteen_evidence_refs(stack, suite) -> None:
    fixture = suite.fixtures["case_review"]
    seeded = await seed_fixture(stack, fixture_id="case_review", fixture=fixture)

    case_id = seeded.case_id("review")
    evidence = await stack.repository.list_evidence_by_case(case_id)
    assert len(evidence) == 13

    findings = await stack.finding_repository.list(case_id)
    assert len(findings) == 1
    links = await stack.finding_repository.list_evidence_links(findings[0].id)
    assert len(links) == 13
    relations = {link.relation for link in links}
    assert relations == {"supports", "contradicts", "context"}


async def test_seed_empty_fixture(stack, suite) -> None:
    fixture = suite.fixtures["case_empty"]
    seeded = await seed_fixture(stack, fixture_id="case_empty", fixture=fixture)
    case_id = seeded.case_id("empty")

    assert await stack.social.list_posts_by_case(case_id) == []
    assert await stack.repository.list_claims_by_case(case_id) == []
    assert await stack.finding_repository.list(case_id) == []


async def test_seed_is_idempotent_per_fixture_instance(stack, suite) -> None:
    """同一 factor 重复 seed 应各自产生独立 case（评测要求 case 隔离）。"""
    fixture = suite.fixtures["case_empty"]
    first = await seed_fixture(stack, fixture_id="case_empty", fixture=fixture)
    second = await seed_fixture(stack, fixture_id="case_empty", fixture=fixture)
    assert first.case_id("empty") != second.case_id("empty")
