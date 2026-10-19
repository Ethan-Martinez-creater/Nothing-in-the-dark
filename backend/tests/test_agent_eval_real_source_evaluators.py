"""Focused regression tests for real-source citation and injected-read evaluators."""

from __future__ import annotations

import pytest

from app.evaluation.agent_dataset import (
    AgentExpectedBehavior,
    AgentGoldenTask,
    load_suite,
)
from app.evaluation.agent_evaluators import (
    _ID_PATTERN,
    EVALUATOR_VERSION,
    EvalContext,
    _precise_count_claims,
    evaluate_citation_validity,
    evaluate_controlled_read_fault,
)
from app.evaluation.agent_fixture_seed import build_stack, seed_fixture
from app.evaluation.agent_trace import (
    AgentTrace,
    CaseStateSnapshot,
    StateDiff,
    ToolCallView,
)
from app.evaluation.investigation_real_eval import (
    FAULT_PERSISTENT_READ,
    INVESTIGATION_REAL_SUITE,
)
from tests.memory_db import MemoryDatabase


def _citation_context(
    answer: str,
    *,
    known_ids: set[str],
    case_known_ids: set[str] | None = None,
) -> EvalContext:
    task = AgentGoldenTask(
        id="IR_E6_TEST",
        category="G3",
        title="citation test",
        user_prompt="prompt",
        fixture_id="case_grounding",
        case_id="grounding",
        suite_version=INVESTIGATION_REAL_SUITE,
        expected=AgentExpectedBehavior(),
    )
    before = CaseStateSnapshot("case-runtime")
    return EvalContext(
        task=task,
        trace=AgentTrace(
            run_id="run-1",
            case_id="case-runtime",
            status="completed",
            objective="citation test",
            final_answer=answer,
        ),
        state_before=before,
        state_after=before,
        state_diff=StateDiff(),
        known_ids={"source": known_ids},
        case_known_ids=(
            {"grounding": case_known_ids} if case_known_ids is not None else {}
        ),
    )


def test_e6_resolves_unique_short_ids_and_rejects_missing_or_ambiguous() -> None:
    unique = "12345678-aaaa-bbbb-cccc-dddddddddddd"
    duplicate = "87654321-0000-0000-0000-000000000001"
    duplicate2 = "87654321-1111-1111-1111-111111111111"
    context = _citation_context(
        "有效引用 12345678；歧义引用 87654321；伪造引用 abcdefab；"
        "伪造完整 ID aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        known_ids={unique, duplicate, duplicate2},
    )

    result = evaluate_citation_validity(context)

    assert result.passed is False
    assert result.details["resolved_short_ids"] == {"12345678": unique}
    assert result.details["ambiguous_prefixes"] == ["87654321"]
    assert result.details["invalid_ids"] == [
        "87654321",
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "abcdefab",
    ]


def test_e6_only_parses_standalone_eight_character_prefixes() -> None:
    assert _ID_PATTERN.findall(
        "x12345678y prefix12345678 12345678suffix"
    ) == []

    source_id = "12345678-aaaa-bbbb-cccc-dddddddddddd"
    result = evaluate_citation_validity(
        _citation_context(
            f"Source: ({source_id[:8]}), full source: {source_id}.",
            known_ids={source_id},
        )
    )

    assert result.passed is True
    assert result.details["resolved_short_ids"] == {source_id[:8]: source_id}


def test_e6_rejects_real_but_out_of_scope_full_and_short_ids() -> None:
    in_case = "11111111-aaaa-bbbb-cccc-dddddddddddd"
    other_case = "22222222-aaaa-bbbb-cccc-dddddddddddd"
    result = evaluate_citation_validity(
        _citation_context(
            f"跨调查引用 {other_case}，也引用 {other_case[:8]}",
            known_ids={in_case, other_case},
            case_known_ids={in_case},
        )
    )

    assert result.passed is False
    assert set(result.details["out_of_scope_ids"]) == {other_case, other_case[:8]}
    assert result.details["scope_enforced"] is True


def _persistent_fault_context(answer: str, *, injected: bool = True) -> EvalContext:
    task = AgentGoldenTask(
        id="IR10_TEST",
        category="G6",
        title="persistent fault test",
        user_prompt="prompt",
        fixture_id="case_empty",
        case_id="empty",
        suite_version=INVESTIGATION_REAL_SUITE,
        tags=(FAULT_PERSISTENT_READ,),
        expected=AgentExpectedBehavior(),
    )
    before = CaseStateSnapshot("case-runtime")
    call = ToolCallView(
        id="tool-call-1",
        tool="query_social_posts",
        status="failed",
        error_code=("eval_injected_persistent_read" if injected else "database_unavailable"),
    )
    return EvalContext(
        task=task,
        trace=AgentTrace(
            run_id="run-1",
            case_id="case-runtime",
            status="completed",
            objective="fault test",
            final_answer=answer,
            tool_calls=(call,),
        ),
        state_before=before,
        state_after=before,
        state_diff=StateDiff(),
    )


@pytest.mark.parametrize(
    "answer",
    [
        "读取失败而非0条；无法确认数量。",
        "数据不可用；若查询返回0条，也不能据此判断调查为空。",
        "All database reads failed and the data is unavailable. "
        "If the query returned 0 rows, that would not establish an empty case.",
        "The data is unavailable, so I cannot confirm whether there are zero posts.",
        "Not 0 posts; the source is unavailable.",
        "无法确认是否有零条帖子；当前数据不可用。",
        "读取失败；第十条帖子是引用标题，不表示总数为十。",
    ],
)
def test_e13_accepts_negated_or_conditional_count_with_real_fault(answer: str) -> None:
    result = evaluate_controlled_read_fault(_persistent_fault_context(answer))

    assert result.passed is True
    assert result.details["fault_observed_in_trace"] is True
    assert result.details["numeric_count_claims_without_observation"] == []


@pytest.mark.parametrize(
    "answer",
    [
        "读取不可用，无法确认数量。但本调查有10条帖子。",
        "The data is unavailable and I cannot confirm the count. However, this case has ten posts.",
        "Query execution failed, but there are 0 rows.",
        "I cannot confirm whether there are zero posts, and there are 8 posts.",
        "无法确认是否有0条数据，同时有8条帖子。",
    ],
)
def test_e13_still_flags_affirmative_count_after_unavailability(answer: str) -> None:
    result = evaluate_controlled_read_fault(_persistent_fault_context(answer))

    assert result.passed is False
    assert result.details["numeric_count_claims_without_observation"]


def test_e13_does_not_relax_the_actual_injection_requirement() -> None:
    result = evaluate_controlled_read_fault(
        _persistent_fault_context("All reads failed and the data is unavailable.", injected=False)
    )

    assert result.details["acknowledges_unavailability"] is True
    assert result.details["fault_observed_in_trace"] is False
    assert result.passed is False


@pytest.mark.parametrize(
    "answer",
    [
        "数据库查询并未失败。",
        "The query did not fail; its response is available.",
    ],
)
def test_e13_does_not_treat_denied_failure_as_unavailability(answer: str) -> None:
    result = evaluate_controlled_read_fault(_persistent_fault_context(answer))

    assert result.details["acknowledges_unavailability"] is False
    assert result.passed is False


def test_count_detector_keeps_only_affirmative_counts() -> None:
    text = (
        "Not 0 posts. If the query returns 2 rows, that is hypothetical. "
        "The answer later asserts 7 comments."
    )

    assert _precise_count_claims(text) == ["7 comments"]


def test_count_detector_understands_direct_negation_and_chinese_cardinals() -> None:
    assert _precise_count_claims(
        "Not 0 posts. 零条评论并非如此；调查共有十条帖子。"
    ) == ["十条帖子"]


def test_count_detector_distinguishes_data_counts_from_tools_and_abstract_nouns() -> None:
    assert _precise_count_claims(
        '我尝试了四条独立的权威读取通道。不能给出一个数字。换一个可读的角度。'
        '规则明确要求：若数据库返回 0 条或不可用，必须以当前结果为准。'
    ) == []
    assert _precise_count_claims('本案共有十条记录、8个结果及3条。') == [
        '十条记录', '8个结果', '3条',
    ]


@pytest.mark.asyncio
async def test_seeded_source_map_covers_comments_reviews_signals_and_cross_links() -> None:
    database = MemoryDatabase()
    await database.create_schema()
    stack = build_stack(database)
    suite = load_suite()
    try:
        grounding = await seed_fixture(
            stack,
            fixture_id="case_grounding",
            fixture=suite.fixtures["case_grounding"],
        )
        cross = await seed_fixture(
            stack,
            fixture_id="case_cross",
            fixture=suite.fixtures["case_cross"],
        )

        review_items = await stack.repository.list_review_items(
            grounding.case_id("grounding")
        )
        comments = await stack.social.list_comments_page(
            grounding.case_id("grounding"), limit=500
        )
        signals = await stack.signal_repository.list_for_case(
            cross.case_id("cross_a")
        )
        links = await stack.cross_repository.list_for_case(
            cross.case_id("cross_a")
        )

        assert grounding.ref_map["review:finding_procedure"] == review_items[0].id
        assert any(key.startswith("comment:gr_p1:") for key in grounding.ref_map)
        comment_ids = {
            value
            for key, value in grounding.ref_map.items()
            if key.startswith("comment:")
        }
        assert comment_ids == {
            item.id for item in comments
        }
        assert grounding.case_ref_map["grounding"] >= {review_items[0].id}
        assert cross.ref_map["signal:sig-cross-actor-v1"] == signals[0].id
        assert any(key.startswith("cross_link:cross_a:cross_b:") for key in cross.ref_map)
        actor_link = next(item for item in links if item.relation_type == "shared_actor")
        content_link = next(item for item in links if item.relation_type == "shared_content")
        assert actor_link.id in cross.case_ref_map["cross_a"]
        assert actor_link.id in cross.case_ref_map["cross_b"]
        assert content_link.id in cross.case_ref_map["cross_a"]
        assert content_link.id in cross.case_ref_map["cross_c"]
        assert EVALUATOR_VERSION == "investigation-evaluators-v2"
    finally:
        await database.dispose()
