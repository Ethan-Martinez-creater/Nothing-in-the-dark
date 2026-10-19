from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.evaluation.agent_dataset import load_suite
from app.evaluation.agent_fixture_seed import build_stack, seed_fixture
from app.infrastructure.database import Database

SUITE_VERSION = "investigation_real_eval_v1"
TASK_IDS = [f"IR{index:02d}" for index in range(1, 11)]
FIXTURE_IDS = (
    "case_grounding",
    "case_cross",
    "case_review",
    "case_empty",
    "case_irrelevant",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _task_payloads(suite_root: Path) -> dict[str, dict]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((suite_root / "tasks").glob("IR*.json"))
    }


def test_suite_loads_ten_frozen_tasks_and_integrity_hashes_match() -> None:
    suite = load_suite(suite_version=SUITE_VERSION)
    assert suite.suite_version == SUITE_VERSION
    assert [task.id for task in suite.tasks] == TASK_IDS
    assert suite.manifest["task_count"] == 10
    assert suite.manifest["task_ids"] == TASK_IDS

    manifest_paths = suite.manifest["integrity"]["files_sha256"]
    expected_paths = {
        "backend/tests/fixtures/agent_eval/investigation_real_eval_v1/fixtures.json",
        "backend/tests/fixtures/agent_eval/investigation_real_eval_v1/rubric.json",
        *{
            f"backend/tests/fixtures/agent_eval/investigation_real_eval_v1/tasks/{task_id}.json"
            for task_id in TASK_IDS
        },
        "docs/evaluation/investigation-real-model-eval-v1/task-catalog.md",
        "docs/evaluation/investigation-real-model-eval-v1/RUBRIC.md",
    }
    assert set(manifest_paths) == expected_paths
    aggregate = "\n".join(
        f"{name}:{digest}" for name, digest in sorted(manifest_paths.items())
    ).encode("utf-8")
    assert hashlib.sha256(aggregate).hexdigest() == suite.manifest["integrity"][
        "suite_content_sha256"
    ]
    for relative_path, expected_hash in manifest_paths.items():
        payload = (_repo_root() / relative_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_hash, relative_path


def test_old_fixture_values_and_24_task_suite_remain_unchanged() -> None:
    root = _repo_root()
    original_fixtures = json.loads(
        (root / "backend/tests/fixtures/agent_eval/interview_agent_v1/fixtures.json")
        .read_text(encoding="utf-8")
    )
    suite = load_suite(suite_version=SUITE_VERSION)
    for fixture_id in ("case_grounding", "case_cross", "case_review", "case_empty"):
        assert suite.fixtures[fixture_id] == original_fixtures[fixture_id]

    old_suite = load_suite(suite_version="interview_agent_v1")
    assert len(old_suite.tasks) == 24
    assert len({task.id for task in old_suite.tasks}) == 24


def test_task_contracts_include_behavioral_alternatives_and_bounded_faults() -> None:
    suite = load_suite(suite_version=SUITE_VERSION)
    payloads = _task_payloads(suite.root)
    assert list(payloads) == TASK_IDS

    for task_id, payload in payloads.items():
        expected = payload["expected"]
        assert payload["suite_version"] == SUITE_VERSION
        assert payload["task_version"] == 1
        assert expected["required_tool_groups"] or expected["required_tools"]
        assert all(group and all(group) for group in expected["required_tool_groups"])
        assert any(
            assertion["kind"] == "no_mutation"
            for assertion in expected["required_state_assertions"]
        )
        budget = payload["budgets"]
        assert 0 < budget["task_timeout_seconds"] <= 240
        assert 0 < budget["max_agent_steps"] <= 8
        assert 0 < budget["max_tool_calls"] <= 6

    assert "smoke" in payloads["IR01"]["tags"]
    assert "smoke" in payloads["IR05"]["tags"]
    assert "fault:transient_read" in payloads["IR09"]["tags"]
    assert "query_social_posts" in payloads["IR09"]["expected"]["required_tool_groups"][0]
    assert "fault:persistent_read" in payloads["IR10"]["tags"]
    assert payloads["IR10"]["budgets"]["max_tool_calls"] <= 3

    rubric = json.loads((suite.root / "rubric.json").read_text(encoding="utf-8"))
    assert set(rubric["tasks"]) == set(TASK_IDS)
    assert {dimension["id"] for dimension in rubric["dimensions"]} == {
        "goal_completion",
        "evidence_support",
        "citation_traceability",
        "uncertainty_conflicts",
        "tool_retrieval_discipline",
        "failure_handling",
    }
    assert rubric["tasks"]["IR07"]["source_map"]
    assert rubric["tasks"]["IR08"]["critical_facts"]["claim_to_evidence"]


@pytest.mark.asyncio
async def test_each_unique_fixture_seeds_and_matches_frozen_truth() -> None:
    suite = load_suite(suite_version=SUITE_VERSION)
    for fixture_id in FIXTURE_IDS:
        fixture = suite.fixtures[fixture_id]
        database = Database("sqlite+aiosqlite:///:memory:")
        await database.create_schema()
        try:
            stack = build_stack(database)
            seeded = await seed_fixture(
                stack,
                fixture_id=fixture_id,
                fixture=fixture,
            )
            investigations = fixture["investigations"]
            assert set(seeded.cases) == {item["key"] for item in investigations}

            for investigation in investigations:
                case_key = investigation["key"]
                case_id = seeded.case_id(case_key)
                assert seeded.ref_map[f"case:{case_key}"] == case_id
                posts = investigation.get("posts", [])
                assert await stack.social.count_posts(case_id) == len(posts)
                for post in posts:
                    assert seeded.ref_map[f"post:{post['key']}"]
                for evidence in investigation.get("evidence", []):
                    evidence_id = seeded.ref_map[f"evidence:{evidence['key']}"]
                    if evidence.get("source_post_key"):
                        assert seeded.ref_map[f"post:{evidence['source_post_key']}"]
                    assert evidence_id

            if fixture_id == "case_grounding":
                case_id = seeded.case_id("grounding")
                distribution = dict(await stack.social.count_posts_by_platform(case_id))
                assert distribution == {"weibo": 6, "bilibili": 4}
                assert await stack.social.count_comments(case_id) == 3
                findings = await stack.finding_repository.list(case_id)
                assert {item.kind: item.status for item in findings} == {
                    "verification": "candidate",
                    "opinion": "under_review",
                }
            elif fixture_id == "case_empty":
                case_id = seeded.case_id("empty")
                assert await stack.social.count_posts(case_id) == 0
                assert await stack.repository.list_claims_by_case(case_id) == []
                assert await stack.repository.list_evidence_by_case(case_id) == []
                assert await stack.finding_repository.list(case_id) == []
            elif fixture_id == "case_cross":
                links = await stack.cross_repository.list_for_case(seeded.case_id("cross_a"))
                assert {item.status for item in links} == {"observed", "candidate"}
                assert seeded.ref_map["post:ca_p3"]
                assert seeded.ref_map["post:cb_p1"]
            elif fixture_id == "case_review":
                case_id = seeded.case_id("review")
                evidence = await stack.repository.list_evidence_by_case(case_id)
                assert len(evidence) == 13
                assert sum(item.stance == "supports" for item in evidence) == 5
                assert sum(item.stance == "contradicts" for item in evidence) == 2
                assert sum(item.stance == "context" for item in evidence) == 6
                finding = (await stack.finding_repository.list(case_id))[0]
                assert finding.status == "candidate"
            elif fixture_id == "case_irrelevant":
                target_id = seeded.case_id("grounding")
                distractor_id = seeded.case_id("near_name")
                assert await stack.social.count_posts(target_id) == 10
                assert await stack.social.count_posts(distractor_id) == 2
                assert len(await stack.report_repository.list_for_case(target_id)) == 1
                assert len(await stack.report_repository.list_for_case(distractor_id)) == 1
        finally:
            await database.dispose()
