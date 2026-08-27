"""M17 显式目标、计划图与完成条件测试。

覆盖：状态机与非法转换、DAG 校验（环/自依赖/缺失节点）、ready 集合、
GoalInterpreter 复杂度判定、Planner 能力/预算校验、CompletionVerifier
各标准类型（确定性优先）、API 契约。
"""

from __future__ import annotations

import atexit
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.planning import (
    ASSESSMENT_INSUFFICIENT,
    ASSESSMENT_SATISFIED,
    ASSESSMENT_UNSATISFIED,
    CRITERION_ARTIFACT_EXISTS,
    CRITERION_CITATION_COVERAGE,
    CRITERION_HUMAN_APPROVED,
    CRITERION_METRIC_THRESHOLD,
    CRITERION_SCHEMA_VALID,
    CRITERION_TOOL_SUCCEEDED,
    GOAL_ACTIVE,
    GOAL_CANCELLED,
    GOAL_COMPLETED,
    GOAL_DRAFT,
    GOAL_FAILED,
    GOAL_NEEDS_INPUT,
    STEP_FAILED,
    STEP_PENDING,
    STEP_READY,
    STEP_RUNNING,
    STEP_SKIPPED,
    STEP_SUCCEEDED,
    STEP_WAITING_REVIEW,
    CompletionVerifier,
    CriterionSpec,
    GoalInterpreter,
    PlanDraft,
    Planner,
    PlanningError,
    StepDraft,
    transition_goal,
    transition_step,
)

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-goal-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


# ---- 状态机 ------------------------------------------------------------------


def test_goal_valid_transitions() -> None:
    assert transition_goal(GOAL_DRAFT, GOAL_ACTIVE) == GOAL_ACTIVE
    assert transition_goal(GOAL_ACTIVE, GOAL_NEEDS_INPUT) == GOAL_NEEDS_INPUT
    assert transition_goal(GOAL_NEEDS_INPUT, GOAL_ACTIVE) == GOAL_ACTIVE
    assert transition_goal(GOAL_ACTIVE, GOAL_COMPLETED) == GOAL_COMPLETED
    assert transition_goal(GOAL_ACTIVE, GOAL_CANCELLED) == GOAL_CANCELLED


def test_goal_invalid_transitions() -> None:
    with pytest.raises(PlanningError):
        transition_goal(GOAL_DRAFT, GOAL_COMPLETED)
    with pytest.raises(PlanningError):
        transition_goal(GOAL_COMPLETED, GOAL_ACTIVE)
    with pytest.raises(PlanningError):
        transition_goal(GOAL_FAILED, GOAL_ACTIVE)


def test_step_valid_transitions() -> None:
    assert transition_step(STEP_PENDING, STEP_READY) == STEP_READY
    assert transition_step(STEP_READY, STEP_RUNNING) == STEP_RUNNING
    assert transition_step(STEP_RUNNING, STEP_SUCCEEDED) == STEP_SUCCEEDED
    assert transition_step(STEP_RUNNING, STEP_FAILED) == STEP_FAILED
    assert transition_step(STEP_RUNNING, STEP_WAITING_REVIEW) == STEP_WAITING_REVIEW
    assert transition_step(STEP_FAILED, STEP_PENDING) == STEP_PENDING  # retry


def test_step_invalid_transitions() -> None:
    with pytest.raises(PlanningError):
        transition_step(STEP_PENDING, STEP_RUNNING)
    with pytest.raises(PlanningError):
        transition_step(STEP_SUCCEEDED, STEP_RUNNING)
    with pytest.raises(PlanningError):
        transition_step(STEP_SKIPPED, STEP_PENDING)


# ---- DAG 校验 ----------------------------------------------------------------


def _plan(*pairs: tuple[str, str]) -> PlanDraft:
    keys = sorted({k for pair in pairs for k in pair})
    steps = {
        key: StepDraft(
            step_key=key,
            task="task-" + key,
            agent_capability="coordinator",
            depends_on=tuple(dep for dep, tgt in pairs if tgt == key),
        )
        for key in keys
    }
    return PlanDraft(steps=steps)


def test_dag_validation_passes() -> None:
    plan = _plan(("a", "b"), ("b", "c"))
    plan.validate_dag()
    assert plan.topological_order() == ["a", "b", "c"]


def test_dag_rejects_cycle() -> None:
    plan = _plan(("a", "b"), ("b", "a"))
    with pytest.raises(PlanningError):
        plan.validate_dag()


def test_dag_rejects_self_dependency() -> None:
    plan = _plan(("a", "a"))
    with pytest.raises(PlanningError):
        plan.validate_dag()


def test_dag_rejects_missing_node() -> None:
    plan = PlanDraft(
        steps={
            "a": StepDraft(
                step_key="a", task="t", depends_on=("missing",)
            )
        }
    )
    with pytest.raises(PlanningError):
        plan.validate_dag()


def test_dag_rejects_empty_plan() -> None:
    with pytest.raises(PlanningError):
        PlanDraft(steps={}).validate_dag()


def test_ready_set_respects_dependencies() -> None:
    plan = _plan(("a", "b"), ("b", "c"))
    assert plan.ready_set({"a": STEP_PENDING, "b": STEP_PENDING, "c": STEP_PENDING}) == ["a"]
    assert plan.ready_set({"a": STEP_SUCCEEDED, "b": STEP_PENDING, "c": STEP_PENDING}) == ["b"]
    assert plan.ready_set({"a": STEP_SUCCEEDED, "b": STEP_SUCCEEDED, "c": STEP_PENDING}) == ["c"]
    assert plan.ready_set(
        {"a": STEP_SUCCEEDED, "b": STEP_SUCCEEDED, "c": STEP_SUCCEEDED}
    ) == []


def test_ready_set_excludes_running_steps() -> None:
    plan = _plan(("a", "b"))
    assert plan.ready_set({"a": STEP_RUNNING, "b": STEP_PENDING}) == []


# ---- Goal Interpreter ---------------------------------------------------------


def test_interpreter_simple_goal() -> None:
    draft = GoalInterpreter().interpret("帮我查一下新能源车的销量")
    assert draft.complexity == "simple"
    assert draft.criteria == []


def test_interpreter_complex_goal() -> None:
    draft = GoalInterpreter().interpret("调查事件的传播路径并输出带引用的报告")
    assert draft.complexity == "complex"
    assert any(c.criterion_type == CRITERION_ARTIFACT_EXISTS for c in draft.criteria)
    assert any(c.criterion_type == CRITERION_CITATION_COVERAGE for c in draft.criteria)


def test_interpreter_rejects_empty_request() -> None:
    with pytest.raises(PlanningError):
        GoalInterpreter().interpret("   ")


# ---- Planner ------------------------------------------------------------------


def test_planner_validates_capabilities() -> None:
    planner = Planner().default_capabilities()
    plan = _plan(("a", "b"))
    planner.validate_plan(plan)


def test_planner_rejects_unknown_capability() -> None:
    planner = Planner().default_capabilities()
    plan = PlanDraft(
        steps={
            "a": StepDraft(step_key="a", task="t", agent_capability="not_a_capability")
        }
    )
    with pytest.raises(PlanningError):
        planner.validate_plan(plan)


def test_planner_rejects_unavailable_capability() -> None:
    planner = Planner()
    planner.register("special", available=False)
    plan = PlanDraft(
        steps={
            "a": StepDraft(step_key="a", task="t", agent_capability="special")
        }
    )
    with pytest.raises(PlanningError):
        planner.validate_plan(plan)


def test_planner_rejects_excessive_budget() -> None:
    planner = Planner().default_capabilities()
    plan = PlanDraft(
        steps={
            "a": StepDraft(step_key="a", task="t", budget_max_cost=60),
            "b": StepDraft(step_key="b", task="t", budget_max_cost=60),
        }
    )
    with pytest.raises(PlanningError):
        planner.validate_plan(plan, max_total_budget=100)


# ---- Completion Verifier -------------------------------------------------------


class _FakeEvidence:
    """可编程的证据提供者（dict 查询）。"""

    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    async def __call__(self, kind: str, query: dict[str, object]) -> object:
        if kind == "artifacts":
            return self._data.get("artifacts", [])
        if kind == "artifact":
            artifact_id = str(query.get("artifact_id") or "")
            for artifact in self._data.get("artifacts", []):
                if str(artifact.get("id", "")) == artifact_id:
                    return artifact
            return None
        if kind == "artifact_data":
            return self._data.get("artifact_data", {})
        if kind == "tool_calls":
            return self._data.get("tool_calls", [])
        if kind == "approvals":
            return self._data.get("approvals", [])
        if kind == "evaluations":
            return self._data.get("evaluations", [])
        return None


class _FakeGoal:
    def __init__(self, case_id: str = "case-1") -> None:
        self.case_id = case_id


async def test_verifier_artifact_exists_satisfied() -> None:
    verifier = CompletionVerifier()
    provider = _FakeEvidence(
        {"artifacts": [{"id": "a1", "kind": "report"}]}
    )
    result = await verifier.verify(
        _FakeGoal(),
        [CriterionSpec(CRITERION_ARTIFACT_EXISTS, target={"artifact_kind": "report"})],
        evidence=provider,
    )
    assert result["result"] == ASSESSMENT_SATISFIED


async def test_verifier_artifact_missing_is_unsatisfied() -> None:
    verifier = CompletionVerifier()
    provider = _FakeEvidence({"artifacts": []})
    result = await verifier.verify(
        _FakeGoal(),
        [CriterionSpec(CRITERION_ARTIFACT_EXISTS, target={"artifact_kind": "report"})],
        evidence=provider,
    )
    assert result["result"] == ASSESSMENT_UNSATISFIED
    assert result["gaps"]


async def test_verifier_schema_valid() -> None:
    verifier = CompletionVerifier()
    provider = _FakeEvidence(
        {"artifacts": [{"id": "a1", "title": "x", "content": "y"}]}
    )
    result = await verifier.verify(
        _FakeGoal(),
        [
            CriterionSpec(
                CRITERION_SCHEMA_VALID,
                target={"artifact_id": "a1", "required_fields": ["title", "content"]},
            )
        ],
        evidence=provider,
    )
    assert result["result"] == ASSESSMENT_SATISFIED
    missing = await verifier.verify(
        _FakeGoal(),
        [
            CriterionSpec(
                CRITERION_SCHEMA_VALID,
                target={"artifact_id": "a1", "required_fields": ["missing_field"]},
            )
        ],
        evidence=provider,
    )
    assert missing["result"] == ASSESSMENT_UNSATISFIED


async def test_verifier_citation_coverage_threshold() -> None:
    verifier = CompletionVerifier()
    provider = _FakeEvidence(
        {"artifact_data": {"cited": 10, "resolved": 10}}
    )
    result = await verifier.verify(
        _FakeGoal(),
        [
            CriterionSpec(
                CRITERION_CITATION_COVERAGE,
                target={"artifact_id": "a1", "min_coverage": 0.95},
            )
        ],
        evidence=provider,
    )
    assert result["result"] == ASSESSMENT_SATISFIED
    provider_low = _FakeEvidence(
        {"artifact_data": {"cited": 10, "resolved": 5}}
    )
    result_low = await verifier.verify(
        _FakeGoal(),
        [
            CriterionSpec(
                CRITERION_CITATION_COVERAGE,
                target={"artifact_id": "a1", "min_coverage": 0.95},
            )
        ],
        evidence=provider_low,
    )
    assert result_low["result"] == ASSESSMENT_UNSATISFIED


async def test_verifier_tool_succeeded() -> None:
    verifier = CompletionVerifier()
    provider = _FakeEvidence(
        {"tool_calls": [{"id": "c1", "status": "completed"}]}
    )
    result = await verifier.verify(
        _FakeGoal(),
        [CriterionSpec(CRITERION_TOOL_SUCCEEDED, target={"tool_name": "verify_claims"})],
        evidence=provider,
    )
    assert result["result"] == ASSESSMENT_SATISFIED


async def test_verifier_human_approved() -> None:
    verifier = CompletionVerifier()
    provider = _FakeEvidence(
        {"approvals": [{"id": "ap1", "status": "approved"}]}
    )
    result = await verifier.verify(
        _FakeGoal(),
        [CriterionSpec(CRITERION_HUMAN_APPROVED)],
        evidence=provider,
    )
    assert result["result"] == ASSESSMENT_SATISFIED
    provider_none = _FakeEvidence({"approvals": []})
    result_none = await verifier.verify(
        _FakeGoal(),
        [CriterionSpec(CRITERION_HUMAN_APPROVED)],
        evidence=provider_none,
    )
    assert result_none["result"] == ASSESSMENT_UNSATISFIED


async def test_verifier_metric_threshold() -> None:
    verifier = CompletionVerifier()
    provider = _FakeEvidence(
        {"evaluations": [{"score": 0.99}]}
    )
    result = await verifier.verify(
        _FakeGoal(),
        [
            CriterionSpec(
                CRITERION_METRIC_THRESHOLD,
                target={"metric": "citation_correctness", "operator": ">=", "threshold": 0.98},
            )
        ],
        evidence=provider,
    )
    assert result["result"] == ASSESSMENT_SATISFIED


async def test_verifier_insufficient_evidence() -> None:
    verifier = CompletionVerifier()
    provider = _FakeEvidence({"artifacts": []})
    result = await verifier.verify(
        _FakeGoal(),
        [
            CriterionSpec(
                CRITERION_SCHEMA_VALID,
                target={"artifact_id": "none"},
            )
        ],
        evidence=provider,
    )
    # artifact None -> insufficient（evidence 缺失），required 时算证据不足
    assert result["result"] in {ASSESSMENT_UNSATISFIED, ASSESSMENT_INSUFFICIENT}


async def test_verifier_requires_provider() -> None:
    verifier = CompletionVerifier()
    with pytest.raises(PlanningError):
        await verifier.verify(_FakeGoal(), [CriterionSpec(CRITERION_ARTIFACT_EXISTS)])


# ---- API 契约 ----------------------------------------------------------------


def _client() -> TestClient:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{_tmp_db()}",
        demo_mode=True,
    )
    return TestClient(create_app(settings))


def test_goal_plan_lifecycle_api() -> None:
    with _client() as client:
        # 创建案件
        case = client.post(
            "/api/v1/cases",
            json={"title": "目标测试", "topic": "新能源", "platforms": ["weibo"]},
        )
        assert case.status_code in {200, 201}
        case_id = case.json()["id"]
        # 创建目标（复杂）
        goal = client.post(
            f"/api/v1/cases/{case_id}/goals",
            json={"objective": "调查传播路径并输出带引用的报告"},
        )
        assert goal.status_code == 200
        goal_body = goal.json()
        assert goal_body["goal"]["status"] == "active"
        assert goal_body["complexity"] == "complex"
        goal_id = goal_body["goal"]["id"]
        # 列表
        listing = client.get(f"/api/v1/cases/{case_id}/goals")
        assert listing.status_code == 200
        assert any(g["id"] == goal_id for g in listing.json())
        # 创建计划（a -> b -> c）
        plan = client.post(
            f"/api/v1/goals/{goal_id}/plans",
            json={
                "steps": [
                    {"step_key": "a", "task": "采集", "agent_capability": "social_crawl"},
                    {
                        "step_key": "b",
                        "task": "分析",
                        "agent_capability": "opinion",
                        "depends_on": ["a"],
                    },
                    {
                        "step_key": "c",
                        "task": "报告",
                        "agent_capability": "report",
                        "depends_on": ["b"],
                    },
                ],
                "edges": [
                    {"source_step_key": "a", "target_step_key": "b"},
                    {"source_step_key": "b", "target_step_key": "c"},
                ],
            },
        )
        assert plan.status_code == 200
        plan_body = plan.json()
        plan_version_id = plan_body["plan_version"]["id"]
        step_ids = plan_body["step_id_by_key"]
        assert set(step_ids) == {"a", "b", "c"}
        # 计划详情：ready 集合
        detail = client.get(f"/api/v1/goals/plans/{plan_version_id}")
        assert detail.status_code == 200
        assert detail.json()["ready_steps"] == ["a"]
        assert detail.json()["topological_order"] == ["a", "b", "c"]
        # 声明步骤：a 成功 -> b ready
        declare_a = client.post(
            f"/api/v1/goals/plans/{plan_version_id}/steps/{step_ids['a']}/declare",
            json={"action": "succeed"},
        )
        assert declare_a.status_code == 200
        assert declare_a.json()["status"] == "succeeded"
        detail2 = client.get(f"/api/v1/goals/plans/{plan_version_id}")
        assert detail2.json()["ready_steps"] == ["b"]
        # 跳过需要理由
        skip_b = client.post(
            f"/api/v1/goals/plans/{plan_version_id}/steps/{step_ids['b']}/declare",
            json={"action": "skip"},
        )
        assert skip_b.status_code == 400
        skip_b_ok = client.post(
            f"/api/v1/goals/plans/{plan_version_id}/steps/{step_ids['b']}/declare",
            json={"action": "skip", "reason": "任务不再需要"},
        )
        assert skip_b_ok.status_code == 200
        assert skip_b_ok.json()["status"] == "skipped"
        detail3 = client.get(f"/api/v1/goals/plans/{plan_version_id}")
        assert detail3.json()["ready_steps"] == ["c"]
        # 证据（M17 引用验证）：悬空引用被拒绝，真实 artifact 成功
        ev_bad = client.post(
            f"/api/v1/goals/plans/{plan_version_id}/steps/{step_ids['c']}/evidence",
            json={"evidence_type": "artifact", "ref_id": "art-1", "ref_kind": "artifact"},
        )
        assert ev_bad.status_code in {400, 404}

        async def _create_report_artifact() -> str:
            container = client.app.state.container
            record = await container.repository.create_artifact(
                case_id=case_id, kind="report", title="报告", data={"ok": True}
            )
            return record.id

        artifact_id = client.portal.call(_create_report_artifact)
        ev = client.post(
            f"/api/v1/goals/plans/{plan_version_id}/steps/{step_ids['c']}/evidence",
            json={
                "evidence_type": "artifact",
                "ref_id": artifact_id,
                "ref_kind": "artifact",
            },
        )
        assert ev.status_code == 200
        # 完成评估：report artifact 不存在 -> 未完成（不伪装成功）
        assess = client.post(
            f"/api/v1/goals/{goal_id}/assess",
            json={"plan_version_id": plan_version_id},
        )
        assert assess.status_code == 200
        assert assess.json()["assessment"]["result"] != "satisfied"
        assert assess.json()["gaps"]


def test_plan_rejects_cycle_via_api() -> None:
    with _client() as client:
        case = client.post(
            "/api/v1/cases",
            json={"title": "环测试", "topic": "话题", "platforms": ["weibo"]},
        )
        case_id = case.json()["id"]
        goal = client.post(
            f"/api/v1/cases/{case_id}/goals",
            json={"objective": "复杂目标：报告与引用"},
        )
        goal_id = goal.json()["goal"]["id"]
        plan = client.post(
            f"/api/v1/goals/{goal_id}/plans",
            json={
                "steps": [
                    {
                        "step_key": "a",
                        "task": "x",
                        "agent_capability": "coordinator",
                        "depends_on": ["b"],
                    },
                    {
                        "step_key": "b",
                        "task": "y",
                        "agent_capability": "coordinator",
                        "depends_on": ["a"],
                    },
                ],
                "edges": [
                    {"source_step_key": "a", "target_step_key": "b"},
                    {"source_step_key": "b", "target_step_key": "a"},
                ],
            },
        )
        assert plan.status_code == 400


def test_goal_cancel_requires_reason() -> None:
    with _client() as client:
        case = client.post(
            "/api/v1/cases",
            json={"title": "取消测试", "topic": "话题", "platforms": ["weibo"]},
        )
        case_id = case.json()["id"]
        goal = client.post(
            f"/api/v1/cases/{case_id}/goals",
            json={"objective": "简单目标"},
        )
        goal_id = goal.json()["goal"]["id"]
        bad = client.post(
            f"/api/v1/goals/{goal_id}/transition",
            json={"target": "cancelled"},
        )
        assert bad.status_code == 400
        ok = client.post(
            f"/api/v1/goals/{goal_id}/transition",
            json={"target": "cancelled", "reason": "用户取消"},
        )
        assert ok.status_code == 200
        assert ok.json()["status"] == "cancelled"
