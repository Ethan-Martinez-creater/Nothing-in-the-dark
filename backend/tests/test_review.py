"""M09 分层人工调查与裁决工作台测试。"""

from __future__ import annotations

import atexit
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.review import (
    DECISION_TO_STATUS,
    ReviewDecision,
    ReviewStateError,
    apply_decision,
    validate_transition,
)

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-rev-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


# ---- 状态机 ----------------------------------------------------------------


def test_decision_to_status_mapping() -> None:
    assert DECISION_TO_STATUS["approved"] == "accepted"
    assert DECISION_TO_STATUS["rejected"] == "rejected"
    assert DECISION_TO_STATUS["edited_approval"] == "accepted"
    assert DECISION_TO_STATUS["more_evidence"] == "needs_more_evidence"
    assert DECISION_TO_STATUS["revoked"] == "unreviewed"


def test_valid_transitions() -> None:
    validate_transition("unreviewed", "in_review")
    validate_transition("in_review", "accepted")
    validate_transition("in_review", "rejected")
    validate_transition("in_review", "needs_more_evidence")
    validate_transition("accepted", "in_review")  # reopen
    validate_transition("accepted", "superseded")


def test_invalid_transition_rejected() -> None:
    with pytest.raises(ReviewStateError):
        validate_transition("unreviewed", "superseded")
    with pytest.raises(ReviewStateError):
        validate_transition("accepted", "needs_more_evidence")


def test_apply_decision() -> None:
    assert apply_decision("in_review", "approved") == "accepted"


def test_decision_requires_reason_for_reject() -> None:
    decision = ReviewDecision(decision="rejected", reason="")
    with pytest.raises(ReviewStateError):
        decision.validate(object_type="claim", current_status="in_review")


def test_decision_edited_approval_requires_patch() -> None:
    decision = ReviewDecision(decision="edited_approval", reason="改一下", structured_patch=None)
    with pytest.raises(ReviewStateError):
        decision.validate(object_type="claim", current_status="in_review")


def test_evidence_patch_restricted() -> None:
    decision = ReviewDecision(
        decision="edited_approval",
        reason="修改标签",
        structured_patch={"content": "不允许编辑原文", "tags": ["x"]},
    )
    with pytest.raises(ReviewStateError):
        decision.validate(object_type="evidence", current_status="in_review")


def test_evidence_patch_allowed_keys() -> None:
    decision = ReviewDecision(
        decision="edited_approval",
        reason="修改纳入状态",
        structured_patch={"tags": ["重要"], "included": False},
    )
    decision.validate(object_type="evidence", current_status="in_review")


# ---- API -----------------------------------------------------------------


def _client() -> TestClient:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{_tmp_db()}",
        demo_mode=True,
    )
    app = create_app(settings)
    return TestClient(app)


def test_api_review_workflow() -> None:
    with _client() as client:
        submitted = client.post(
            "/api/v1/cases/c1/reviews/items",
            json={
                "object_type": "claim",
                "object_id": "claim-1",
                "summary": "某主张待审核",
                "risk_level": "high",
            },
        )
        assert submitted.status_code == 201
        item_id = submitted.json()["id"]

        queue = client.get("/api/v1/cases/c1/reviews/queue")
        assert queue.status_code == 200
        assert queue.json()["total"] == 1
        assert queue.json()["items"][0]["status"] == "unreviewed"

        claimed = client.post(f"/api/v1/cases/c1/reviews/{item_id}:claim")
        assert claimed.status_code == 200
        assert claimed.json()["status"] == "in_review"

        decided = client.post(
            f"/api/v1/cases/c1/reviews/{item_id}/decisions",
            json={"decision": "approved", "reason": "证据充分"},
        )
        assert decided.status_code == 200
        assert decided.json()["status"] == "accepted"

        # 已接受状态不能直接再次 approve（需先 reopen）。
        conflict = client.post(
            f"/api/v1/cases/c1/reviews/{item_id}/decisions",
            json={"decision": "rejected", "reason": "x"},
        )
        assert conflict.status_code == 400

        reopened = client.post(f"/api/v1/cases/c1/reviews/{item_id}:reopen")
        assert reopened.status_code == 200
        assert reopened.json()["status"] == "in_review"


def test_api_review_comments_and_activity() -> None:
    with _client() as client:
        submitted = client.post(
            "/api/v1/cases/c1/reviews/items",
            json={"object_type": "evidence", "object_id": "ev-1"},
        )
        item_id = submitted.json()["id"]
        comment = client.post(
            f"/api/v1/cases/c1/reviews/{item_id}/comments",
            json={"text": "请补充来源", "actor": "operator-a"},
        )
        assert comment.status_code == 201
        comments = client.get(f"/api/v1/cases/c1/reviews/{item_id}/comments")
        assert comments.status_code == 200
        assert len(comments.json()["comments"]) == 1

        activity = client.get("/api/v1/cases/c1/activity")
        assert activity.status_code == 200
        assert len(activity.json()["events"]) >= 1


def test_api_review_double_claim_conflict() -> None:
    with _client() as client:
        submitted = client.post(
            "/api/v1/cases/c1/reviews/items",
            json={"object_type": "claim", "object_id": "claim-2"},
        )
        item_id = submitted.json()["id"]
        first = client.post(f"/api/v1/cases/c1/reviews/{item_id}:claim")
        assert first.json()["status"] == "in_review"
        second = client.post(f"/api/v1/cases/c1/reviews/{item_id}:claim")
        # 已被领取：第二次领取失败（返回 500 或 4xx，不静默成功）。
        assert second.status_code != 200


def test_api_review_case_isolation_and_version_conflict() -> None:
    with _client() as client:
        submitted = client.post(
            "/api/v1/cases/case-a/reviews/items",
            json={"object_type": "claim", "object_id": "claim-scoped"},
        )
        assert submitted.status_code == 201
        item_id = submitted.json()["id"]

        assert client.post(
            f"/api/v1/cases/case-b/reviews/{item_id}:claim"
        ).status_code == 400
        assert client.post(
            f"/api/v1/cases/case-b/reviews/{item_id}/comments",
            json={"text": "cross-case"},
        ).status_code == 400

        claimed = client.post(f"/api/v1/cases/case-a/reviews/{item_id}:claim")
        assert claimed.status_code == 200
        stale = client.post(
            f"/api/v1/cases/case-a/reviews/{item_id}/decisions",
            json={
                "decision": "approved",
                "reason": "ok",
                "expected_version": 999,
            },
        )
        assert stale.status_code == 400
        assert stale.json()["code"] == "review_version_conflict"


# ================= RC1: Generic Review submit 对 finding 强制走原子入口 =========


def _client_with_finding() -> Iterator[tuple[TestClient, str, str]]:
    """创建 demo app + case + candidate finding（context manager）。

    用法：with _client_with_finding() as (client, case_id, finding_id):
    """
    from contextlib import contextmanager

    @contextmanager
    def _ctx() -> Iterator[tuple[TestClient, str, str]]:
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{_tmp_db()}",
            demo_mode=True,
        )
        app = create_app(settings)
        with TestClient(app) as client:
            case = client.post(
                "/api/v1/cases",
                json={"topic": "RC1 案例", "platforms": ["weibo"]},
            )
            case_id = case.json()["id"]
            finding = client.post(
                f"/api/v1/cases/{case_id}/findings",
                json={"statement": "RC1 结论：候选内容待审核"},
            )
            finding_id = finding.json()["id"]
            yield client, case_id, finding_id

    return _ctx()  # type: ignore[return-value]


def test_rc1_generic_submit_routes_finding_to_atomic_path() -> None:
    """Test R1: generic POST /reviews/items(object_type=finding) → 原子入口。

    断言 Finding=under_review、exactly one ReviewItem、status=unreviewed、
    priority/risk_level/queue 兼容、summary 采用 finding.statement 而非客户端输入。
    """
    with _client_with_finding() as (client, case_id, finding_id):
        resp = client.post(
            f"/api/v1/cases/{case_id}/reviews/items",
            json={
                "object_type": "finding",
                "object_id": finding_id,
                "summary": "客户端伪造的摘要",
                "priority": 7,
                "risk_level": "high",
                "queue": "priority",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["object_type"] == "finding"
        assert body["object_id"] == finding_id
        assert body["status"] == "unreviewed"
        assert body["priority"] == 7
        assert body["risk_level"] == "high"
        assert body["queue"] == "priority"
        # canonical summary 来自 finding.statement，客户端伪造文本被忽略
        assert body["summary"] == "RC1 结论：候选内容待审核"
        assert body["summary"] != "客户端伪造的摘要"

        finding = client.get(f"/api/v1/cases/{case_id}/findings/{finding_id}")
        assert finding.status_code == 200
        assert finding.json()["finding"]["status"] == "under_review"

        queue = client.get(
            f"/api/v1/cases/{case_id}/reviews/queue?object_type=finding"
        )
        assert queue.json()["total"] == 1


def test_rc1_generic_submit_idempotent_preserves_metadata() -> None:
    """Test R2: 重复 generic submit 幂等，且不覆盖既有 priority/risk/queue/summary。"""
    with _client_with_finding() as (client, case_id, finding_id):
        first = client.post(
            f"/api/v1/cases/{case_id}/reviews/items",
            json={
                "object_type": "finding",
                "object_id": finding_id,
                "priority": 7,
                "risk_level": "high",
                "queue": "priority",
            },
        )
        assert first.status_code == 201
        item_id = first.json()["id"]

        second = client.post(
            f"/api/v1/cases/{case_id}/reviews/items",
            json={
                "object_type": "finding",
                "object_id": finding_id,
                "summary": "other",
                "priority": 1,
                "risk_level": "low",
                "queue": "other",
            },
        )
        assert second.status_code == 201
        assert second.json()["id"] == item_id
        # metadata 保持首次值
        assert second.json()["priority"] == 7
        assert second.json()["risk_level"] == "high"
        assert second.json()["queue"] == "priority"
        assert second.json()["summary"] == "RC1 结论：候选内容待审核"

        queue = client.get(
            f"/api/v1/cases/{case_id}/reviews/queue?object_type=finding"
        )
        assert queue.json()["total"] == 1
        finding = client.get(f"/api/v1/cases/{case_id}/findings/{finding_id}")
        assert finding.json()["finding"]["status"] == "under_review"


def test_rc1_generic_submit_nonexistent_finding_rejected() -> None:
    """Test R3: generic submit + nonexistent finding → finding_not_found，无 ReviewItem。"""
    with _client_with_finding() as (client, case_id, _finding_id):
        resp = client.post(
            f"/api/v1/cases/{case_id}/reviews/items",
            json={"object_type": "finding", "object_id": "finding-does-not-exist"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "finding_not_found"
        queue = client.get(
            f"/api/v1/cases/{case_id}/reviews/queue?object_type=finding"
        )
        assert queue.json()["total"] == 0


def test_rc1_generic_submit_cross_case_finding_rejected() -> None:
    """Test R4: generic submit + cross-case finding → finding_scope_mismatch。"""
    with _client_with_finding() as (client, case_id, finding_id):
        other = client.post(
            "/api/v1/cases",
            json={"topic": "另一案例", "platforms": ["weibo"]},
        )
        other_id = other.json()["id"]
        resp = client.post(
            f"/api/v1/cases/{other_id}/reviews/items",
            json={"object_type": "finding", "object_id": finding_id},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "finding_scope_mismatch"
        # other case 无 ReviewItem，原 case Finding 状态不变
        queue = client.get(f"/api/v1/cases/{other_id}/reviews/queue?object_type=finding")
        assert queue.json()["total"] == 0
        finding = client.get(f"/api/v1/cases/{case_id}/findings/{finding_id}")
        assert finding.json()["finding"]["status"] == "candidate"


def test_rc1_generic_submit_verified_finding_reruns_review() -> None:
    """Test R5: verified Finding 经 generic submit 进入复审（复用同一 item）。"""
    with _client_with_finding() as (client, case_id, finding_id):
        # 构造真实流程：candidate → submit → claim → approve → verified/accepted
        sub = client.post(
            f"/api/v1/cases/{case_id}/reviews/items",
            json={"object_type": "finding", "object_id": finding_id},
        )
        item_id = sub.json()["id"]
        claimed = client.post(f"/api/v1/cases/{case_id}/reviews/{item_id}:claim")
        assert claimed.json()["status"] == "in_review"
        decided = client.post(
            f"/api/v1/cases/{case_id}/reviews/{item_id}/decisions",
            json={"decision": "approved", "reason": "通过"},
        )
        assert decided.json()["status"] == "accepted"
        finding = client.get(f"/api/v1/cases/{case_id}/findings/{finding_id}")
        assert finding.json()["finding"]["status"] == "verified"

        # generic submit 复审
        reopened = client.post(
            f"/api/v1/cases/{case_id}/reviews/items",
            json={"object_type": "finding", "object_id": finding_id},
        )
        assert reopened.status_code == 201
        assert reopened.json()["id"] == item_id  # 复用同一 item
        assert reopened.json()["status"] == "in_review"
        finding = client.get(f"/api/v1/cases/{case_id}/findings/{finding_id}")
        assert finding.json()["finding"]["status"] == "under_review"
        queue = client.get(f"/api/v1/cases/{case_id}/reviews/queue?object_type=finding")
        assert queue.json()["total"] == 1