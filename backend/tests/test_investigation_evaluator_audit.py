from types import SimpleNamespace

from app.scripts.audit_investigation_evaluators import repair_archived_refs

CASE = "10000000-0000-4000-8000-000000000001"
POST = "20000000-0000-4000-8000-000000000001"
COMMENT = "30000000-0000-4000-8000-000000000001"
FINDING = "40000000-0000-4000-8000-000000000001"


def inputs():
    return (
        {"ref_map": {"case:a": CASE, "post:p": POST, "finding:f": FINDING},
         "final_answer": "invented 90000000-0000-4000-8000-000000000001", "observations": []},
        SimpleNamespace(case_id="a", tags=("read_only",)),
        {"investigations": [{"key": "a", "posts": [{"key": "p", "comments": [
            {"native_id": "comment1", "content": "fixture text"}
        ]}], "findings": [{"key": "f"}], "review_items": [
            {"object_type": "finding", "object_id_key": "f"}]}]},
    )


def test_archive_audit_only_repairs_fixture_matching_successful_observations():
    trace, task, fixture = inputs()
    trace["observations"] = [{
        "tool": "query_social_comments", "status": "succeeded", "arguments": {"case_id": CASE},
        "result": {"ok": True, "comments": [{
            "id": COMMENT, "post_id": POST, "native_id": "comment1", "content": "fixture text"
        }]},
    }]
    refs, permitted = repair_archived_refs(trace, task, fixture)
    assert refs["comment:comment1"] == COMMENT
    assert COMMENT in permitted
    assert "90000000-0000-4000-8000-000000000001" not in refs.values()


def test_archive_audit_rejects_wrong_post_or_content():
    for change in ({"post_id": CASE}, {"content": "wrong"}, {"native_id": "unknown"}):
        trace, task, fixture = inputs()
        comment = {"id": COMMENT, "post_id": POST,
                   "native_id": "comment1", "content": "fixture text", **change}
        trace["observations"] = [{
            "tool": "query_social_comments", "status": "succeeded",
            "arguments": {"case_id": CASE}, "result": {"comments": [comment]},
        }]
        refs, _ = repair_archived_refs(trace, task, fixture)
        assert COMMENT not in refs.values()


def test_archive_audit_does_not_launder_an_unrelated_review_object():
    trace, task, fixture = inputs()
    trace["observations"] = [{
        "tool": "query_review_items", "status": "succeeded", "arguments": {"case_id": CASE},
        "result": {"ok": True, "review_items": [{
            "id": COMMENT, "object_type": "finding", "object_id": POST
        }]},
    }]
    refs, _ = repair_archived_refs(trace, task, fixture)
    assert COMMENT not in refs.values()


def test_archive_audit_rejects_failed_observations_and_wrong_scope():
    for status, ok, case_id in (("failed", True, CASE), ("succeeded", False, CASE),
                                ("succeeded", True, POST)):
        trace, task, fixture = inputs()
        trace["observations"] = [{
            "tool": "query_social_comments", "status": status,
            "arguments": {"case_id": case_id}, "result": {"ok": ok, "comments": [{
                "id": COMMENT, "post_id": POST,
                "native_id": "comment1", "content": "fixture text",
            }]},
        }]
        refs, _ = repair_archived_refs(trace, task, fixture)
        assert COMMENT not in refs.values()


def test_archive_audit_repairs_review_only_for_frozen_reviewable_finding():
    trace, task, fixture = inputs()
    trace["observations"] = [{
        "tool": "query_review_items", "status": "succeeded", "arguments": {"case_id": CASE},
        "result": {"ok": True, "review_items": [{
            "id": COMMENT, "object_type": "finding", "object_id": FINDING,
        }]},
    }]
    refs, permitted = repair_archived_refs(trace, task, fixture)
    assert refs["review_item:f"] == COMMENT
    assert COMMENT in permitted
