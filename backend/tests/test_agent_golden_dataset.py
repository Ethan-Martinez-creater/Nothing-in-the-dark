"""interview_agent_v1 Golden Dataset 的契约测试。

验证数据集本身合法、引用完整、工具名与真实 Tool Registry 一致。
不执行 agent、不调模型、不连外部系统。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluation.agent_dataset import (
    ASSERTION_KINDS,
    CATEGORIES,
    CATEGORY_SAFETY_APPROVAL,
    EXPECTED_TASK_COUNT,
    SUITE_VERSION,
    TASKS_PER_CATEGORY,
    AgentDatasetError,
    AgentEvalBudget,
    AgentExpectedBehavior,
    AgentGoldenTask,
    StateAssertion,
    fixtures_root,
    load_suite,
    validate_suite,
)
from app.application.repositories import ApplicationRepository
from app.harness.database_tools import register_database_tools
from app.harness.intelligence_tools import register_intelligence_tools
from app.harness.skills import SkillRegistry
from app.harness.tool_factory import build_tool_registry
from app.infrastructure.crawler.demo import DemoCrawlerAdapter
from app.infrastructure.database import Database
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.embeddings import EmbeddingWorkerClient


def _real_tool_names(tmp_path: Path) -> set[str]:
    """从真实 Tool Registry 取出工具名（不使用 Eval 专用替身）。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'registry.db'}")
    repository = ApplicationRepository(database)
    registry = build_tool_registry(
        DemoCrawlerAdapter(),
        SkillRegistry(),
        KnowledgeRepository(database),
        EmbeddingWorkerClient("http://localhost:1", dimensions=1024, timeout_seconds=1),
        SocialRepository(database),
        repository,
    )
    # 生产 bootstrap 同样注册这两组只读工具包（service=None 仅影响执行期可用性）。
    register_database_tools(registry, None)
    register_intelligence_tools(registry, None)
    return registry.names()


@pytest.fixture(scope="module")
def suite():
    return load_suite()


def test_suite_shape_is_frozen(suite) -> None:
    """24 个任务、6 类各 4 个、版本固定。"""
    assert suite.suite_version == SUITE_VERSION
    assert len(suite.tasks) == EXPECTED_TASK_COUNT
    counts = suite.category_counts()
    assert set(counts) == set(CATEGORIES)
    for category, count in counts.items():
        assert count == TASKS_PER_CATEGORY, category


def test_task_ids_are_unique_and_ordered(suite) -> None:
    ids = [task.id for task in suite.tasks]
    assert len(ids) == len(set(ids))
    # 文件名排序即类别顺序，便于人工逐条审查。
    assert ids == sorted(ids)


def test_suite_validation_has_no_problems(suite, tmp_path: Path) -> None:
    problems = validate_suite(suite, known_tools=_real_tool_names(tmp_path))
    assert problems == []


def test_referenced_tools_exist_in_registry(suite, tmp_path: Path) -> None:
    """required/optional/forbidden 必须是真实注册的工具名。"""
    known = _real_tool_names(tmp_path)
    referenced: set[str] = set()
    for task in suite.tasks:
        referenced |= set(task.expected.required_tools)
        referenced |= set(task.expected.optional_tools)
        referenced |= set(task.expected.forbidden_tools)
    unknown = sorted(referenced - known)
    assert unknown == [], f"unknown tools referenced: {unknown}"


def test_safety_category_is_critical(suite) -> None:
    """G5 全部为 critical，且 critical 只出现在 G5。"""
    safety = suite.by_category(CATEGORY_SAFETY_APPROVAL)
    assert len(safety) == TASKS_PER_CATEGORY
    assert all(task.critical for task in safety)
    others = [t for t in suite.tasks if t.category != CATEGORY_SAFETY_APPROVAL]
    assert not any(task.critical for task in others)


def test_every_read_only_task_asserts_no_mutation(suite) -> None:
    """只读任务必须具备 no_mutation 断言（防止静默写库）。"""
    for task in suite.tasks:
        read_only = "read_only" in task.tags
        if not read_only:
            continue
        kinds = {item.kind for item in task.expected.required_state_assertions}
        assert "no_mutation" in kinds, f"{task.id} missing no_mutation assertion"


def test_fixture_references_resolve(suite) -> None:
    """task.fixture_id 与 task.case_id 必须能在 fixtures.json 中解析。"""
    for task in suite.tasks:
        assert task.fixture_id in suite.fixtures, task.id
        fixture = suite.fixtures[task.fixture_id]
        keys = {item["key"] for item in fixture.get("investigations", [])}
        if task.case_id:
            assert task.case_id in keys, f"{task.id}: case_id {task.case_id} not in {sorted(keys)}"


def test_fixture_internal_references_resolve(suite) -> None:
    """fixture 内部引用的 post / claim / evidence / finding key 必须存在。"""
    for name, fixture in suite.fixtures.items():
        if name.startswith("_"):
            continue
        for investigation in fixture.get("investigations", []):
            posts = {p["key"] for p in investigation.get("posts", [])}
            claims = {c["key"] for c in investigation.get("claims", [])}
            evidence = {e["key"] for e in investigation.get("evidence", [])}
            findings = {f["key"] for f in investigation.get("findings", [])}
            for item in investigation.get("evidence", []):
                if item.get("source_post_key"):
                    assert item["source_post_key"] in posts, (
                        f"{name}/{item['key']}: unknown post {item['source_post_key']}"
                    )
                if item.get("claim_key"):
                    assert item["claim_key"] in claims, (
                        f"{name}/{item['key']}: unknown claim {item['claim_key']}"
                    )
            for finding in investigation.get("findings", []):
                for ref in finding.get("evidence_refs", []):
                    assert ref["evidence_key"] in evidence, (
                        f"{name}/{finding['key']}: unknown evidence {ref['evidence_key']}"
                    )
            for item in investigation.get("review_items", []):
                if item.get("object_id_key"):
                    assert item["object_id_key"] in findings | evidence | claims, (
                        f"{name}/{item['key']}: unknown object {item['object_id_key']}"
                    )

        # 跨调查链接与信号引用的 case 必须存在
        case_keys = {item["key"] for item in fixture.get("investigations", [])}
        for link in fixture.get("cross_links", []):
            assert link["left_case_key"] in case_keys, f"{name}: bad left_case_key"
            assert link["right_case_key"] in case_keys, f"{name}: bad right_case_key"
        for signal in fixture.get("signals", []):
            assert signal["case_key"] in case_keys, f"{name}: bad signal case_key"
            for related in signal.get("related_case_keys", []):
                assert related in case_keys, f"{name}: bad related_case_key"


def test_fixture_has_no_live_network_dependency(suite) -> None:
    """冻结 fixture 不得包含真实平台 URL（评测不允许联网采集）。"""
    raw = json.dumps(suite.fixtures, ensure_ascii=False)
    for forbidden in ("http://", "https://", "weibo.com", "bilibili.com", "zhihu.com"):
        assert forbidden not in raw, f"fixture contains live-network reference: {forbidden}"


def test_expected_behavior_roundtrip() -> None:
    """schema 序列化往返保持一致（Replay / 报告依赖）。"""
    task = AgentGoldenTask(
        id="GX_99",
        category=CATEGORIES[0],
        title="roundtrip",
        user_prompt="prompt",
        fixture_id="case_grounding",
        case_id="grounding",
        critical=False,
        tags=("read_only",),
        expected=AgentExpectedBehavior(
            required_tools=("query_claims",),
            optional_tools=("query_evidence",),
            forbidden_tools=("collect_social_posts",),
            required_artifact_types=("report",),
            expected_case_scope="grounding",
            required_state_assertions=(
                StateAssertion(kind="no_mutation", target="case", expected=True),
            ),
            expected_citation_refs=("ev_detection",),
            answer_must_contain=("批次",),
            answer_must_not_contain=("已确认",),
            requires_human_escalation=False,
        ),
        budgets=AgentEvalBudget(max_agent_steps=6, max_tool_calls=4),
    )
    restored = AgentGoldenTask.from_dict(task.to_dict())
    assert restored == task
    assert restored.to_dict() == task.to_dict()
    assert {
        "id",
        "suite_version",
        "category",
        "title",
        "user_prompt",
        "fixture_id",
        "expected",
        "budgets",
    } <= set(task.to_dict())


def test_validation_rejects_broken_tasks(suite) -> None:
    """校验器必须能抓到 required/forbidden 冲突与未知断言 kind。"""
    broken = AgentGoldenTask(
        id="GX_bad",
        category=CATEGORIES[0],
        title="broken",
        user_prompt="p",
        fixture_id="case_grounding",
        expected=AgentExpectedBehavior(
            required_tools=("query_claims",),
            forbidden_tools=("query_claims",),
            required_state_assertions=(StateAssertion(kind="not_a_real_kind"),),
        ),
    )
    from dataclasses import replace

    bad_suite = replace(suite, tasks=(broken,))
    problems = validate_suite(bad_suite)
    assert any("both required and forbidden" in item for item in problems)
    assert any("unknown state assertion kind" in item for item in problems)


def test_assertion_kinds_are_declared() -> None:
    assert "no_mutation" in ASSERTION_KINDS
    assert "artifact_exists" in ASSERTION_KINDS
    assert "tool_call_status" in ASSERTION_KINDS


def test_missing_suite_raises(tmp_path: Path) -> None:
    with pytest.raises(AgentDatasetError):
        load_suite(tmp_path, suite_version="does_not_exist")


def test_fixtures_root_points_at_frozen_dir() -> None:
    root = fixtures_root() / SUITE_VERSION
    assert (root / "manifest.json").exists()
    assert (root / "fixtures.json").exists()
    assert (root / "tasks").is_dir()
