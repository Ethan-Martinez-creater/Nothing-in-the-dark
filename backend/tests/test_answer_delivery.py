import pytest

from app.evaluation.answer_delivery import assess_answer_delivery


def _assess(**overrides):
    values = {
        "final_answer": "已完成调查。",
        "status": "completed",
        "task_tags": (),
        "policy_audit": (),
        "injected_failure_count": 0,
    }
    values.update(overrides)
    return assess_answer_delivery(**values)


def test_empty_answer_is_not_delivered():
    result = _assess(final_answer=" \n ")

    assert result == {
        "version": "answer-delivery-v1",
        "answer_origin": "NONE",
        "delivery_status": "NOT_DELIVERED",
        "structural_pass": False,
        "semantic_acceptance": "MANUAL_REVIEW_REQUIRED",
    }


def test_cancelled_run_with_partial_text_is_interrupted():
    result = _assess(status="cancelled", final_answer="已经查到两条帖子，但尚未比较。")

    assert result["answer_origin"] == "MODEL_FINAL"
    assert result["delivery_status"] == "INTERRUPTED"
    assert result["structural_pass"] is False
    assert result["semantic_acceptance"] == "MANUAL_REVIEW_REQUIRED"


@pytest.mark.parametrize(
    "runtime_message",
    [
        "本次运行已达到工具调用上限（已执行 6 次）；结果可能不完整。",
        "只读执行策略阻止了写入工具，已安全停止。",
        "工具请求包含未知工具或重复标识，已安全停止。",
    ],
)
def test_legacy_runtime_stop_messages_are_not_delivered_answers(runtime_message):
    result = _assess(final_answer=runtime_message)

    assert result["answer_origin"] == "RUNTIME_STOP"
    assert result["delivery_status"] == "NOT_DELIVERED"
    assert result["structural_pass"] is False


def test_persistent_read_refusal_requires_real_injected_failure_and_review():
    result = _assess(
        final_answer="精确统计读取持续不可用，无法统计帖子总数或平台分布。",
        task_tags=("read_only", "fault:persistent_read", "failure_handling"),
        injected_failure_count=3,
    )

    assert result["answer_origin"] == "MODEL_FINAL"
    assert result["delivery_status"] == "EXPECTED_REFUSAL_REQUIRES_REVIEW"
    assert result["structural_pass"] is True
    assert result["semantic_acceptance"] == "MANUAL_REVIEW_REQUIRED"


def test_persistent_read_tag_without_an_observed_fault_is_not_expected_refusal():
    result = _assess(
        final_answer="目前无法核实具体总量。",
        task_tags=("fault:persistent_read",),
        injected_failure_count=0,
    )

    assert result["delivery_status"] == "MODEL_ANSWER_REQUIRES_REVIEW"
    assert result["semantic_acceptance"] == "MANUAL_REVIEW_REQUIRED"


def test_successful_finalization_identifies_synthesis_even_with_budget_context():
    result = _assess(
        final_answer=(
            "本次运行已达到工具调用上限，所以无法继续查找；根据已读取的两条记录，"
            "目前只能确认其中一条来自微博，不能据此推断总体趋势。"
        ),
        policy_audit=(
            {"event_type": "answer_finalization_start"},
            {"event_type": "answer_finalization_end", "outcome": "succeeded"},
        ),
    )

    assert result["answer_origin"] == "MODEL_SYNTHESIS"
    assert result["delivery_status"] == "MODEL_ANSWER_REQUIRES_REVIEW"
    assert result["structural_pass"] is True
    assert result["semantic_acceptance"] == "MANUAL_REVIEW_REQUIRED"


def test_failed_finalization_fallback_is_not_mistaken_for_model_answer():
    result = _assess(
        final_answer="本次运行已达到工具调用上限；结果可能不完整。",
        policy_audit=(
            {"event_type": "answer_finalization_start"},
            {"event_type": "answer_finalization_end", "outcome": "failed"},
        ),
    )

    assert result["answer_origin"] == "RUNTIME_STOP"
    assert result["delivery_status"] == "NOT_DELIVERED"
    assert result["structural_pass"] is False


def test_nonempty_citation_does_not_imply_semantic_acceptance():
    result = _assess(final_answer="帖子 12345678 证明了总体趋势。")

    assert result["delivery_status"] == "MODEL_ANSWER_REQUIRES_REVIEW"
    assert result["semantic_acceptance"] == "MANUAL_REVIEW_REQUIRED"
