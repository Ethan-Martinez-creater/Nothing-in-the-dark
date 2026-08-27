"""M21 广义人工介入与反馈闭环测试。

覆盖：审批状态机（合法/非法/终态/幂等）、ApprovalPolicyEngine 风险策略
（fail closed / 自动批准 / 策略例外）、一次性执行授权（绑定校验/篡改
拒绝/过期/重复消费）、API 契约（收件箱/决策/过期清理/统计）。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.harness.approval_policy import (
    APPROVAL_APPROVED,
    APPROVAL_APPROVED_WITH_EDITS,
    APPROVAL_BUDGET_INCREASE,
    APPROVAL_CANCELLED,
    APPROVAL_CONSUMED,
    APPROVAL_EXPIRED,
    APPROVAL_PENDING,
    APPROVAL_POLICY_EXCEPTION,
    APPROVAL_PUBLISH,
    APPROVAL_REJECTED,
    APPROVAL_TOOL_EXECUTION,
    RISK_CRITICAL,
    RISK_HIGH,
    RISK_LOW,
    ApprovalPolicyDecision,
    ApprovalPolicyEngine,
    ApprovalRequest,
    approval_is_terminal,
    validate_approval_transition,
)
from app.main import create_app
from app.schemas.cases import CreateCaseRequest

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-hitl-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


# ---- 状态机 ------------------------------------------------------------------


def test_valid_transitions() -> None:
    assert validate_approval_transition(
        APPROVAL_PENDING, APPROVAL_APPROVED
    ) == APPROVAL_APPROVED
    assert validate_approval_transition(
        APPROVAL_PENDING, APPROVAL_APPROVED_WITH_EDITS
    ) == APPROVAL_APPROVED_WITH_EDITS
    assert validate_approval_transition(
        APPROVAL_PENDING, APPROVAL_REJECTED
    ) == APPROVAL_REJECTED
    assert validate_approval_transition(
        APPROVAL_APPROVED, APPROVAL_CONSUMED
    ) == APPROVAL_CONSUMED


def test_illegal_transitions() -> None:
    with pytest.raises(ValueError):
        validate_approval_transition(APPROVAL_PENDING, APPROVAL_CONSUMED)
    with pytest.raises(ValueError):
        validate_approval_transition(APPROVAL_REJECTED, APPROVAL_APPROVED)
    with pytest.raises(ValueError):
        validate_approval_transition(APPROVAL_EXPIRED, APPROVAL_PENDING)
    with pytest.raises(ValueError):
        validate_approval_transition(APPROVAL_CONSUMED, APPROVAL_REJECTED)


def test_terminal_states() -> None:
    assert approval_is_terminal(APPROVAL_REJECTED)
    assert approval_is_terminal(APPROVAL_EXPIRED)
    assert approval_is_terminal(APPROVAL_CANCELLED)
    assert approval_is_terminal(APPROVAL_CONSUMED)
    assert not approval_is_terminal(APPROVAL_PENDING)
    assert not approval_is_terminal(APPROVAL_APPROVED)


# ---- ApprovalPolicyEngine ----------------------------------------------------


def _request(
    tool: str = "collect_social_posts",
    approval_type: str = APPROVAL_TOOL_EXECUTION,
    risk_level: str = RISK_HIGH,
) -> ApprovalRequest:
    return ApprovalRequest(
        actor="operator",
        case_id="case-1",
        tool=tool,
        approval_type=approval_type,
        risk_level=risk_level,
        scope="case",
        requested_action="crawl",
        arguments_summary="platforms=[weibo]",
    )


def test_engine_default_fail_closed() -> None:
    engine = ApprovalPolicyEngine()
    decision = engine.decide(_request())
    assert decision.verdict == "require_approval"
    assert decision.policy_version == engine.POLICY_VERSION


def test_engine_policy_exception_never_auto() -> None:
    engine = ApprovalPolicyEngine()
    decision = engine.decide(
        _request(approval_type=APPROVAL_POLICY_EXCEPTION, risk_level=RISK_LOW)
    )
    assert decision.verdict == "require_approval"


def test_engine_publish_never_auto() -> None:
    engine = ApprovalPolicyEngine()
    decision = engine.decide(
        _request(approval_type=APPROVAL_PUBLISH, risk_level=RISK_LOW)
    )
    assert decision.verdict == "require_approval"
    assert decision.risk_level == RISK_CRITICAL


def test_engine_auto_approve_readonly_low_risk() -> None:
    engine = ApprovalPolicyEngine()
    decision = engine.decide(
        _request(
            tool="search_social_evidence",
            approval_type=APPROVAL_TOOL_EXECUTION,
            risk_level=RISK_LOW,
        )
    )
    assert decision.verdict == "auto_approve"


def test_engine_budget_increase_requires_approval() -> None:
    engine = ApprovalPolicyEngine()
    decision = engine.decide(
        _request(approval_type=APPROVAL_BUDGET_INCREASE, risk_level=RISK_HIGH)
    )
    assert decision.verdict == "require_approval"


def test_engine_classify_tool() -> None:
    engine = ApprovalPolicyEngine()
    assert engine.classify_tool(
        "collect_social_posts", side_effect="external_read"
    ) == (APPROVAL_TOOL_EXECUTION, RISK_HIGH)
    assert engine.classify_tool(
        "expensive_tool", side_effect="none", estimated_cost=5.0
    ) == (APPROVAL_BUDGET_INCREASE, RISK_HIGH)
    assert engine.classify_tool(
        "notify_external", side_effect="external_write"
    ) == (APPROVAL_PUBLISH, RISK_CRITICAL)


def test_engine_allowed_decisions() -> None:
    engine = ApprovalPolicyEngine()
    assert "edit_and_approve" in engine.default_allowed_decisions(
        APPROVAL_TOOL_EXECUTION
    )
    assert "edit_and_approve" not in engine.default_allowed_decisions(
        APPROVAL_PUBLISH
    )


# ---- API 契约 ----------------------------------------------------------------


def _client() -> TestClient:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{_tmp_db()}",
        demo_mode=True,
    )
    return TestClient(create_app(settings))


def _seeded_approval_client() -> tuple[TestClient, str, str]:
    """预置 run + pending approval，返回 (client, run_id, approval_id)。

    避免依赖 worker/LLM：直接在同一个 sqlite 库中写入 run 与审批，
    TestClient 使用同一数据库 URL，仅测 API 决策面。
    """
    import asyncio

    db_path = _tmp_db()
    db_url = f"sqlite+aiosqlite:///{db_path}"

    async def seed() -> tuple[str, str]:
        from app.bootstrap import ApplicationContainer

        container = ApplicationContainer(Settings(database_url=db_url, demo_mode=True))
        await container.database.create_schema()
        try:
            case = await container.repository.create_case(
                CreateCaseRequest(title="审批测试", topic="话题", platforms=["weibo"])
            )
            run = await container.repository.create_agent_run(
                case_id=case.id, turn_id=None, objective="采集", agent="coordinator"
            )
            approval = await container.repository.create_approval(
                run_id=run.id,
                action="collect_social_posts",
                reason="需要审批",
                request_payload={
                    "approval_kind": "collect",
                    "arguments_summary": "platforms=[weibo]",
                },
            )
            return run.id, approval.id
        finally:
            await container.database.dispose()

    run_id, approval_id = asyncio.run(seed())
    client = TestClient(
        create_app(Settings(database_url=db_url, demo_mode=True))
    )
    return client, run_id, approval_id


def test_approval_inbox_and_decision_api() -> None:
    client, run_id, approval_id = _seeded_approval_client()
    with client:
        # 收件箱列出 pending 审批
        inbox = client.get(f"/api/v1/approvals?run_id={run_id}")
        assert inbox.status_code == 200
        records = inbox.json()
        assert any(
            r["run_id"] == run_id and r["status"] == "pending" for r in records
        )
        # 详情
        detail = client.get(f"/api/v1/approvals/{approval_id}")
        assert detail.status_code == 200
        assert detail.json()["id"] == approval_id
        # 决策：reject
        decided = client.post(
            f"/api/v1/approvals/{approval_id}:decide",
            json={"decision": "reject", "note": "暂不采集", "actor": "operator"},
        )
        assert decided.status_code == 200
        assert decided.json()["status"] == "rejected"
        # 幂等：同决策再次调用返回 200（恢复语义）
        again = client.post(
            f"/api/v1/approvals/{approval_id}:decide",
            json={"decision": "reject"},
        )
        assert again.status_code == 200
        # 不同决策拒绝（409）
        conflict = client.post(
            f"/api/v1/approvals/{approval_id}:decide",
            json={"decision": "approve"},
        )
        assert conflict.status_code == 409


def test_edit_and_approve_requires_arguments() -> None:
    client, run_id, approval_id = _seeded_approval_client()
    with client:
        # 缺 edited_action -> 400
        bad = client.post(
            f"/api/v1/approvals/{approval_id}:decide",
            json={"decision": "edit_and_approve"},
        )
        assert bad.status_code == 400
        # 编辑不能改工具
        tool_changed = client.post(
            f"/api/v1/approvals/{approval_id}:decide",
            json={
                "decision": "edit_and_approve",
                "edited_action": {
                    "tool": "write_case_memory",
                    "arguments": {"platforms": ["weibo"]},
                },
            },
        )
        assert tool_changed.status_code == 400


def test_approval_stats_and_expire() -> None:
    with _client() as client:
        stats = client.get("/api/v1/approvals/stats/summary")
        assert stats.status_code == 200
        assert "approval_rate" in stats.json()
        exp = client.post("/api/v1/approvals/expire-overdue")
        assert exp.status_code == 200
        assert exp.json()["expired"] == 0


def test_policy_endpoint_behavior() -> None:
    # 策略引擎独立于 API 可用（fail closed 默认）。
    engine = ApprovalPolicyEngine()
    decision = engine.decide(_request())
    assert isinstance(decision, ApprovalPolicyDecision)
    assert decision.to_dict()["verdict"] == "require_approval"


# ---- 一次性执行授权 ------------------------------------------------------------


def test_execution_authorization_consume_semantics() -> None:
    """授权绑定 tool+参数哈希+run+期限；篡改/过期/重复消费均拒绝。"""
    import asyncio

    async def main() -> None:
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{_tmp_db()}",
            demo_mode=True,
        )
        from app.bootstrap import ApplicationContainer

        container = ApplicationContainer(settings)
        await container.database.create_schema()
        try:
            case = await container.repository.create_case(
                CreateCaseRequest(
                    title="授权测试", topic="话题", platforms=["weibo"]
                )
            )
            run = await container.repository.create_agent_run(
                case_id=case.id,
                turn_id=None,
                objective="采集",
                agent="coordinator",
            )
            approval = await container.repository.create_approval(
                run_id=run.id,
                action="collect_social_posts",
                reason="需要审批",
                request_payload={"approval_kind": "collect"},
            )
            arguments = {"platforms": ["weibo"], "keywords": ["新能源"]}
            argument_hash = hashlib.sha256(
                json.dumps(
                    arguments, sort_keys=True, ensure_ascii=False
                ).encode("utf-8")
            ).hexdigest()
            token = "test-token-123"
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            await container.repository.create_execution_authorization(
                approval_id=approval.id,
                run_id=run.id,
                tool_name="collect_social_posts",
                argument_hash=argument_hash,
                token_hash=token_hash,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            # 并发消费也只能有一个成功（单条条件 UPDATE）。
            results = await asyncio.gather(
                *[
                    container.repository.consume_execution_authorization(
                        token_hash=token_hash,
                        run_id=run.id,
                        tool_name="collect_social_posts",
                        argument_hash=argument_hash,
                    )
                    for _ in range(5)
                ]
            )
            assert sum(results) == 1
            # 后续重复消费拒绝。
            again = await container.repository.consume_execution_authorization(
                token_hash=token_hash,
                run_id=run.id,
                tool_name="collect_social_posts",
                argument_hash=argument_hash,
            )
            assert not again
            # 参数篡改拒绝（独立审批，一票一用）
            other_hash = hashlib.sha256(b"other").hexdigest()
            approval2 = await container.repository.create_approval(
                run_id=run.id,
                action="collect_social_posts",
                reason="需要审批2",
                request_payload={"approval_kind": "collect"},
            )
            token2 = "test-token-456"
            await container.repository.create_execution_authorization(
                approval_id=approval2.id,
                run_id=run.id,
                tool_name="collect_social_posts",
                argument_hash=argument_hash,
                token_hash=hashlib.sha256(token2.encode("utf-8")).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            tampered = await container.repository.consume_execution_authorization(
                token_hash=hashlib.sha256(token2.encode("utf-8")).hexdigest(),
                run_id=run.id,
                tool_name="collect_social_posts",
                argument_hash=other_hash,
            )
            assert not tampered
            # 过期拒绝（独立审批）
            approval3 = await container.repository.create_approval(
                run_id=run.id,
                action="collect_social_posts",
                reason="需要审批3",
                request_payload={"approval_kind": "collect"},
            )
            token3 = "test-token-789"
            await container.repository.create_execution_authorization(
                approval_id=approval3.id,
                run_id=run.id,
                tool_name="collect_social_posts",
                argument_hash=argument_hash,
                token_hash=hashlib.sha256(token3.encode("utf-8")).hexdigest(),
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            expired = await container.repository.consume_execution_authorization(
                token_hash=hashlib.sha256(token3.encode("utf-8")).hexdigest(),
                run_id=run.id,
                tool_name="collect_social_posts",
                argument_hash=argument_hash,
            )
            assert not expired
            # 工具不匹配拒绝（独立审批）
            approval4 = await container.repository.create_approval(
                run_id=run.id,
                action="collect_social_posts",
                reason="需要审批4",
                request_payload={"approval_kind": "collect"},
            )
            token4 = "test-token-abc"
            await container.repository.create_execution_authorization(
                approval_id=approval4.id,
                run_id=run.id,
                tool_name="collect_social_posts",
                argument_hash=argument_hash,
                token_hash=hashlib.sha256(token4.encode("utf-8")).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            wrong_tool = await container.repository.consume_execution_authorization(
                token_hash=hashlib.sha256(token4.encode("utf-8")).hexdigest(),
                run_id=run.id,
                tool_name="write_case_memory",
                argument_hash=argument_hash,
            )
            assert not wrong_tool
        finally:
            await container.database.dispose()

    asyncio.run(main())


def test_expire_pending_approvals() -> None:
    import asyncio

    async def main() -> None:
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{_tmp_db()}",
            demo_mode=True,
        )
        from app.bootstrap import ApplicationContainer

        container = ApplicationContainer(settings)
        await container.database.create_schema()
        try:
            case = await container.repository.create_case(
                CreateCaseRequest(
                    title="过期测试", topic="话题", platforms=["weibo"]
                )
            )
            run = await container.repository.create_agent_run(
                case_id=case.id, turn_id=None, objective="x"
            )
            approval = await container.repository.create_approval(
                run_id=run.id, action="collect_social_posts", reason="r",
                request_payload={},
            )
            await container.repository.update_approval_full(
                approval.id,
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
            count = await container.repository.expire_pending_approvals()
            assert count == 1
            updated = await container.repository.get_approval(approval.id)
            assert updated.status == "expired"
            # 已过期审批不可再决策
            with pytest.raises(ValueError):
                await container.repository.update_approval_full(
                    approval.id, status=APPROVAL_APPROVED, decision="approve"
                )
        finally:
            await container.database.dispose()

    asyncio.run(main())
