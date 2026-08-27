"""M09 分层人工调查与裁决工作台测试。"""

from __future__ import annotations

import atexit
import shutil
import uuid
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