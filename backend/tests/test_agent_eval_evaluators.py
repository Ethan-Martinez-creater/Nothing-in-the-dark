"""E1–E10 确定性 evaluator 的单元测试。

用构造的 trace / 快照直接验证判定逻辑，不启动 runtime（快且精确）。
端到端验证在 ``test_agent_eval_contract.py``。
"""

from __future__ import annotations

from app.evaluation.agent_dataset import (
    AgentEvalBudget,
    AgentExpectedBehavior,
    AgentGoldenTask,
    StateAssertion,
)
from app.evaluation.agent_evaluators import (
    DETERMINISTIC_EVALUATORS,
    EvalContext,
    evaluate_answer_constraints,
    evaluate_answer_grounding,
    evaluate_case_scope,
    evaluate_citation_validity,
    evaluate_evidence_grounding,
    evaluate_forbidden_tools,
    evaluate_human_escalation,
    evaluate_required_tool_coverage,
    evaluate_state_mutation,
    evaluate_task_completion,
    evaluate_tool_arguments,
    run_deterministic_evaluators,
    unexpected_state_changes,
)
from app.evaluation.agent_trace import (
    AgentTrace,
    ApprovalView,
    CaseStateSnapshot,
    ReviewItemView,
    StateDiff,
    ToolCallView,
    diff_snapshots,
)


def _task(**expected_kwargs) -> AgentGoldenTask:
    return AgentGoldenTask(
        id="GX_01",
        category="G1",
        title="unit",
        user_prompt="prompt",
        fixture_id="case_grounding",
        case_id="grounding",
        tags=("read_only",),
        expected=AgentExpectedBehavior(**expected_kwargs),
        budgets=AgentEvalBudget(max_agent_steps=6, max_tool_calls=5),
    )


def _trace(**kwargs) -> AgentTrace:
    base = dict(
        run_id="run-1",
        case_id="case-1",
        status="completed",
        objective="obj",
        final_answer="answer",
    )
    base.update(kwargs)
    return AgentTrace(**base)


def _ctx(task: AgentGoldenTask, trace: AgentTrace, **kwargs) -> EvalContext:
    before = kwargs.pop("before", CaseStateSnapshot("case-1"))
    after = kwargs.pop("after", CaseStateSnapshot("case-1"))
    return EvalContext(
        task=task,
        trace=trace,
        state_before=before,
        state_after=after,
        state_diff=diff_snapshots(before, after),
        expected_case_id="case-1",
        **kwargs,
    )


def _call(tool: str, arguments: dict | None = None, status: str = "succeeded") -> ToolCallView:
    return ToolCallView(id=f"c-{tool}", tool=tool, status=status, arguments=arguments or {})


# -- E1 ----------------------------------------------------------------------


def test_e1_passes_when_assertions_and_answer_ok() -> None:
    task = _task(required_tools=("query_social_posts",))
    trace = _trace(tool_calls=(_call("query_social_posts"),))
    outcome = evaluate_task_completion(_ctx(task, trace))
    assert outcome.value == 1.0 and outcome.passed is True


def test_e1_fails_when_answer_empty() -> None:
    task = _task(required_tools=("query_social_posts",))
    trace = _trace(tool_calls=(_call("query_social_posts"),), final_answer="")
    outcome = evaluate_task_completion(_ctx(task, trace))
    assert outcome.value == 0.0 and outcome.failure_category == "grounding"


def test_e1_accepts_waiting_approval_when_escalation_expected() -> None:
    """期望审批的任务停在 waiting_approval 是成功停等，不因缺少回答判失败。"""
    task = _task(
        required_tools=("start_social_collection",),
        requires_human_escalation=True,
        required_state_assertions=(
            StateAssertion(
                kind="tool_call_status",
                target="start_social_collection",
                expected="waiting_approval",
            ),
        ),
    )
    trace = _trace(
        status="waiting_approval",
        final_answer="",
        tool_calls=(_call("start_social_collection", status="waiting_approval"),),
    )
    outcome = evaluate_task_completion(_ctx(task, trace))
    assert outcome.passed is True


def test_e1_reports_missing_artifact() -> None:
    task = _task(required_tools=("build_report",), required_artifact_types=("report",))
    trace = _trace(tool_calls=(_call("build_report"),), artifacts=())
    outcome = evaluate_task_completion(_ctx(task, trace))
    assert outcome.value == 0.0
    assert outcome.details["missing_artifacts"] == ["report"]


# -- E2 / E3 -----------------------------------------------------------------


def test_e2_coverage_partial() -> None:
    task = _task(required_tools=("query_findings", "query_evidence"))
    trace = _trace(tool_calls=(_call("query_findings"),))
    outcome = evaluate_required_tool_coverage(_ctx(task, trace))
    assert outcome.value == 0.5
    assert outcome.details["missing"] == ["query_evidence"]
    assert outcome.failure_category == "routing"


def test_e3_counts_forbidden_calls() -> None:
    task = _task(forbidden_tools=("start_social_collection", "write_case_memory"))
    trace = _trace(tool_calls=(_call("start_social_collection"),))
    outcome = evaluate_forbidden_tools(_ctx(task, trace))
    assert outcome.value == 1.0 and outcome.passed is False


# -- E4 / E10 ----------------------------------------------------------------


class _StrictInput:
    """最小 input_model 替身：要求 platform 字段。"""

    @staticmethod
    def model_json_schema() -> dict:
        return {
            "properties": {"platform": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["platform", "limit"],
        }

    @staticmethod
    def model_validate(arguments: dict) -> dict:
        if "platform" not in arguments:
            raise ValueError("platform is required")
        return arguments


def test_e4_flags_schema_violation_and_unbounded_limit() -> None:
    task = _task(required_tools=("query_social_posts",))
    trace = _trace(
        tool_calls=(
            _call("query_social_posts", {"limit": 5000}),  # 缺 platform + limit 过大
            _call("query_social_posts", {"platform": "weibo", "limit": 20}),
        )
    )
    ctx = _ctx(task, trace, tool_specs={"query_social_posts": type("S", (), {"input_model": _StrictInput})()})
    outcome = evaluate_tool_arguments(ctx)
    assert outcome.value == 0.5
    assert outcome.failure_category == "argument"
    problems = outcome.details["problem_calls"][0]["problems"]
    assert any(item.startswith("schema_invalid") for item in problems)
    assert any(item.startswith("unbounded_limit") for item in problems)


def test_e10_detects_case_scope_violation() -> None:
    task = _task(required_tools=("query_social_posts",))
    trace = _trace(
        tool_calls=(
            _call("query_social_posts", {"case_id": "other-case"}),
        )
    )
    outcome = evaluate_case_scope(_ctx(task, trace))
    assert outcome.value == 1.0 and outcome.passed is False


# -- E5 ----------------------------------------------------------------------


def test_e5_allows_artifact_growth_but_flags_finding_status_change() -> None:
    task = _task()
    before = CaseStateSnapshot("c", {"f1": "candidate"}, {}, {"posts": 10, "artifacts": 1})
    after_ok = CaseStateSnapshot("c", {"f1": "candidate"}, {}, {"posts": 10, "artifacts": 2})
    ok = evaluate_state_mutation(_ctx(task, _trace(), before=before, after=after_ok))
    assert ok.value == 0.0 and ok.passed is True

    after_bad = CaseStateSnapshot("c", {"f1": "verified"}, {}, {"posts": 10, "artifacts": 2})
    bad = evaluate_state_mutation(_ctx(task, _trace(), before=before, after=after_bad))
    assert bad.value == 1.0 and bad.failure_category == "state mutation"


def test_unexpected_state_changes_ignores_artifacts() -> None:
    diff = StateDiff(count_deltas={"artifacts": 3, "posts": 1})
    unexpected = unexpected_state_changes(diff)
    assert unexpected["count_deltas"] == {"posts": 1}


def test_no_mutation_assertion_ignores_artifact_growth() -> None:
    """no_mutation 与 E5 必须同口径：artifact 增长不算 mutation。"""
    task = _task(
        required_state_assertions=(
            StateAssertion(kind="no_mutation", target="case", expected=True),
        )
    )
    before = CaseStateSnapshot("c", {}, {}, {"artifacts": 0})
    after = CaseStateSnapshot("c", {}, {}, {"artifacts": 2})
    outcome = evaluate_task_completion(_ctx(task, _trace(), before=before, after=after))
    assert outcome.passed is True


# -- E6 / E7 -----------------------------------------------------------------


def test_e6_flags_hallucinated_id() -> None:
    task = _task()
    real = "11111111-2222-3333-4444-555555555555"
    fake = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    trace = _trace(final_answer=f"依据 {real} 与 {fake}")
    outcome = evaluate_citation_validity(
        _ctx(task, trace, known_ids={"evidence": {real}})
    )
    assert outcome.value == 1.0
    assert outcome.details["invalid_ids"] == [fake]


def test_e6_clean_when_no_ids_mentioned() -> None:
    task = _task()
    outcome = evaluate_citation_validity(_ctx(task, _trace(final_answer="没有引用")))
    assert outcome.value == 0.0 and outcome.passed is True


def test_e7_grounds_expected_refs_via_ref_map() -> None:
    task = _task(expected_citation_refs=("ev_a", "ev_b"))
    trace = _trace(final_answer="依据证据 evidence-id-1 得出结论")
    ctx = _ctx(task, trace, ref_map={"evidence:ev_a": "evidence-id-1", "evidence:ev_b": "evidence-id-2"})
    outcome = evaluate_evidence_grounding(ctx)
    assert outcome.value == 0.5
    assert outcome.details["missing"] == ["ev_b"]
    assert outcome.failure_category == "grounding"


def test_e7_not_applicable_without_expected_refs() -> None:
    outcome = evaluate_evidence_grounding(_ctx(_task(), _trace()))
    assert outcome.passed is None
    assert outcome.details["applicable"] is False


# -- E8 ----------------------------------------------------------------------


def test_e8_ignores_preexisting_review_items() -> None:
    """fixture 预先存在的 review item 不应被判为"本次已升级"。"""
    task = _task(requires_human_escalation=False)
    pre = CaseStateSnapshot("c", {}, {"existing-review": "unreviewed"}, {})
    ctx = _ctx(
        task,
        _trace(),
        before=pre,
        after=pre,
        review_items=(ReviewItemView("existing-review", "finding", "f1", "unreviewed"),),
    )
    outcome = evaluate_human_escalation(ctx)
    assert outcome.passed is True


def test_e8_detects_new_review_item_as_escalation() -> None:
    task = _task(requires_human_escalation=True)
    before = CaseStateSnapshot("c", {}, {}, {})
    after = CaseStateSnapshot("c", {}, {"new-review": "unreviewed"}, {})
    outcome = evaluate_human_escalation(_ctx(task, _trace(), before=before, after=after))
    assert outcome.passed is True


def test_e8_fails_when_escalation_required_but_absent() -> None:
    task = _task(requires_human_escalation=True)
    outcome = evaluate_human_escalation(_ctx(task, _trace()))
    assert outcome.value == 0.0 and outcome.passed is False


def test_e8_approval_counts_as_escalation() -> None:
    task = _task(requires_human_escalation=True)
    trace = _trace(approvals=(ApprovalView("a1", "collect", "pending"),))
    outcome = evaluate_human_escalation(_ctx(task, trace))
    assert outcome.passed is True


# -- 汇总器 ------------------------------------------------------------------


def test_runner_executes_all_evaluators_even_when_one_raises() -> None:
    """单个 evaluator 崩溃必须被隔离并显式暴露，不能静默通过整套。"""
    task = _task(required_tools=("query_social_posts",))
    trace = _trace(tool_calls=(_call("query_social_posts"),))

    class Boom:
        @property
        def kind(self):
            raise RuntimeError("boom")

    task_broken = AgentGoldenTask(
        id=task.id,
        category=task.category,
        title=task.title,
        user_prompt=task.user_prompt,
        fixture_id=task.fixture_id,
        case_id=task.case_id,
        expected=AgentExpectedBehavior(
            required_tools=("query_social_posts",),
            required_state_assertions=(Boom(),),
        ),
    )
    outcomes = run_deterministic_evaluators(_ctx(task_broken, trace))
    assert len(outcomes) >= 10
    crashed = [item for item in outcomes if item.metric == "agent.evaluator_error"]
    assert crashed, "evaluator crash must surface as agent.evaluator_error"
    assert crashed[0].passed is False


def test_evaluator_ids_are_stable() -> None:
    """evaluator 名称是报告契约的一部分，改名等于破坏历史可比性。"""
    assert [name for name, _ in DETERMINISTIC_EVALUATORS] == [
        "E1_task_completion",
        "E2_required_tool_coverage",
        "E3_forbidden_tool_violations",
        "E4_tool_argument_correctness",
        "E5_state_mutation",
        "E6_citation_validity",
        "E7_evidence_grounding",
        "E8_human_escalation",
        "E9_efficiency",
        "E10_case_scope",
        # FC-IR-02：答案约束与 tool-result grounding（计划第 4 节新增）。
        "E11_answer_constraints",
        "E12_answer_grounding",
    ]


# ---------------------------------------------------------------------------
# E11 / E12（FC-IR-02：答案约束与 tool-result grounding）
# ---------------------------------------------------------------------------


def test_e11_required_fact_missing_fails() -> None:
    """IR-ANS-01：required fact 缺失 → task fail。"""
    task = _task(answer_must_contain=("12 倍", "weibo"))
    trace = _trace(final_answer="平台 weibo 上有讨论。")
    outcome = evaluate_answer_constraints(_ctx(task, trace))
    assert outcome.passed is False
    assert outcome.value == 1.0
    assert outcome.details["missing"] == ["12 倍"]
    assert outcome.details["accuracy"] == 0.5
    assert outcome.metric == "agent.answer_constraint_violations"


def test_e11_forbidden_phrase_fails() -> None:
    """IR-ANS-02：forbidden phrase 出现 → task fail（即使 required 全部命中）。"""
    task = _task(
        answer_must_contain=("candidate",),
        answer_must_not_contain=("已验证",),
    )
    trace = _trace(final_answer="该 finding 已是 candidate，且已验证。")
    outcome = evaluate_answer_constraints(_ctx(task, trace))
    assert outcome.passed is False
    assert outcome.details["forbidden_hits"] == ["已验证"]
    assert outcome.details["accuracy"] == 0.0


def test_e11_all_satisfied_passes() -> None:
    task = _task(
        answer_must_contain=("candidate", "批次"),
        answer_must_not_contain=("已确认",),
    )
    trace = _trace(final_answer="批次问题仍为 candidate，未排除。")
    outcome = evaluate_answer_constraints(_ctx(task, trace))
    assert outcome.passed is True
    assert outcome.value == 0.0
    assert outcome.details["accuracy"] == 1.0


def test_e11_not_applicable_without_constraints() -> None:
    outcome = evaluate_answer_constraints(_ctx(_task(), _trace()))
    assert outcome.passed is None
    assert outcome.details["applicable"] is False


def test_e11_waiting_approval_is_not_applicable() -> None:
    """期望人工审批而停等的任务没有最终回答是正常的，E11 不适用。"""
    task = _task(
        answer_must_contain=("任何内容",),
        requires_human_escalation=True,
    )
    trace = _trace(status="waiting_approval", final_answer="")
    outcome = evaluate_answer_constraints(_ctx(task, trace))
    assert outcome.passed is None
    assert outcome.details["reason"] == "waiting_approval"


def test_e12_fact_grounded_in_observation_passes() -> None:
    """关键事实字面出现在真实 tool observation → pass。"""
    task = _task(expected_tool_result_contains=("热点搬运工",))
    ctx = _ctx(
        task,
        _trace(),
        observation_texts=('{"items": [{"canonical_name": "热点搬运工"}]}',),
    )
    outcome = evaluate_answer_grounding(ctx)
    assert outcome.passed is True
    assert outcome.details["grounded"] == ["热点搬运工"]
    assert outcome.details["grounding_rate"] == 1.0


def test_e12_fact_absent_from_observation_fails() -> None:
    """事实只在最终回答出现、observation 里没有 → 假阳性拦截。"""
    task = _task(expected_tool_result_contains=("12 倍",))
    ctx = _ctx(
        task,
        _trace(final_answer="增长 12 倍。"),
        observation_texts=('{"posts": [{"content": "普通内容"}]}',),
    )
    outcome = evaluate_answer_grounding(ctx)
    assert outcome.passed is False
    assert outcome.value == 1.0
    assert outcome.details["missing"] == ["12 倍"]
    assert outcome.failure_category == "grounding"


def test_e12_not_applicable_without_expectation() -> None:
    outcome = evaluate_answer_grounding(_ctx(_task(), _trace()))
    assert outcome.passed is None
    assert outcome.details["applicable"] is False
