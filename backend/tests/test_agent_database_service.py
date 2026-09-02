"""AgentDatabaseReadService + 新增只读 Repository 方法测试（DBT2/DBT3/DBT9）。

覆盖文档 R01–R20（Repository 只读能力）与 9 个 DB Tool 的 Service 逻辑
（不含 LLM）：精确数量、分页、case scope、exact-ID 跨 Case 隔离、日期聚合、
评论经 SourcePost JOIN 的 case scope、ReviewDecision 经 ReviewItem 的
case scope、实时可见性（freshness）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.application.agent_database_service import AgentDatabaseReadService
from app.application.repositories import ApplicationRepository
from app.infrastructure.database.collection_run_repository import CollectionRunRepository
from app.infrastructure.database.finding_repository import FindingRepository
from app.infrastructure.database.models import (
    CaseActivityLogRecord,
    FindingRecord,
    ReportDocumentRecord,
    ReviewDecisionRecord,
    ReviewItemRecord,
)
from app.infrastructure.database.report_repository import ReportDocumentRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.schemas.cases import CreateCaseRequest
from tests.memory_db import MemoryDatabase


def _post(
    platform: str,
    index: int,
    *,
    content: str | None = None,
    author: str | None = None,
    published_at: str = "2026-08-15T10:00:00+08:00",
    comments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "platform": platform,
        "native_id": f"{platform}-{index}",
        "content_type": "post",
        "title": "",
        "content": content or f"{platform} 竹知了事件相关讨论内容 {index}",
        "author": author or f"author-{platform}",
        "published_at": published_at,
        "engagement": 10,
        "metrics": {"total": 10},
        "url": f"https://example.com/{platform}/{index}",
        "raw": {"id": f"{platform}-{index}"},
        "comments": comments or [],
    }


def _comment(index: int, *, content: str = "评论内容") -> dict[str, Any]:
    return {
        "native_id": f"c-{index}",
        "content": f"{content} {index}",
        "author_id": f"ca-{index}",
        "author_name": f"commenter-{index}",
        "published_at": "2026-08-16T10:00:00+08:00",
        "metrics": {"likes": 1},
    }


async def _setup() -> (
    tuple[
        MemoryDatabase,
        Any,
        AgentDatabaseReadService,
        SocialRepository,
        ApplicationRepository,
        FindingRepository,
        ReportDocumentRepository,
        CollectionRunRepository,
    ]
):
    database = MemoryDatabase()
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(
        CreateCaseRequest(
            topic="华为竹知了事件",
            platforms=["weibo", "zhihu"],
            time_start="2026-08-10",
            time_end="2026-08-20",
        )
    )
    social = SocialRepository(database)
    finding_repo = FindingRepository(database)
    report_repo = ReportDocumentRepository(database)
    collection_repo = CollectionRunRepository(database)
    service = AgentDatabaseReadService(
        repository=app_repo,
        social_repository=social,
        collection_run_repository=collection_repo,
        finding_repository=finding_repo,
        report_repository=report_repo,
    )
    return database, case, service, social, app_repo, finding_repo, report_repo, collection_repo


# ---------------------------------------------------------------------------
# R01–R15: SocialRepository 只读能力
# ---------------------------------------------------------------------------


async def test_r01_count_posts_current_case_only() -> None:
    db, case, _, social, app_repo, *_ = await _setup()
    other = await app_repo.create_case(
        CreateCaseRequest(topic="其它事件", platforms=["weibo"])
    )
    await social.persist_batch(case_id=case.id, posts=[_post("weibo", 1)])
    await social.persist_batch(case_id=other.id, posts=[_post("weibo", 9)])
    assert await social.count_posts(case.id) == 1
    assert await social.count_posts(other.id) == 1
    await db.dispose()


async def test_r02_posts_multi_platform_filter() -> None:
    db, case, _, social, *_ = await _setup()
    await social.persist_batch(
        case_id=case.id,
        posts=[_post("weibo", 1), _post("zhihu", 1), _post("zhihu", 2)],
    )
    assert await social.count_posts(case.id, platforms=["zhihu"]) == 2
    posts = await social.list_posts_page(case.id, platforms=["zhihu"])
    assert {p.platform for p in posts} == {"zhihu"}
    await db.dispose()


async def test_r03_posts_lexical_query() -> None:
    db, case, _, social, *_ = await _setup()
    await social.persist_batch(
        case_id=case.id,
        posts=[
            _post("weibo", 1, content="华为要求停售竹知了"),
            _post("zhihu", 1, content="普通讨论"),
        ],
    )
    assert await social.count_posts(case.id, q="竹知了") == 1
    assert await social.count_posts(case.id, q="不存在的词") == 0
    await db.dispose()


async def test_r04_posts_author_filter() -> None:
    db, case, _, social, *_ = await _setup()
    await social.persist_batch(
        case_id=case.id,
        posts=[_post("weibo", 1, author="张三"), _post("zhihu", 1, author="李四")],
    )
    assert await social.count_posts(case.id, author="张三") == 1
    await db.dispose()


async def test_r05_posts_date_range() -> None:
    db, case, _, social, *_ = await _setup()
    await social.persist_batch(
        case_id=case.id,
        posts=[
            _post("weibo", 1, published_at="2026-08-10T00:00:00+08:00"),
            _post("weibo", 2, published_at="2026-08-18T00:00:00+08:00"),
        ],
    )
    from_ = datetime(2026, 8, 12, tzinfo=UTC)
    to = datetime(2026, 8, 20, tzinfo=UTC)
    assert await social.count_posts(case.id, date_from=from_, date_to=to) == 1
    await db.dispose()


async def test_r06_posts_sort_newest_oldest() -> None:
    db, case, _, social, *_ = await _setup()
    await social.persist_batch(
        case_id=case.id,
        posts=[
            _post("weibo", 1, published_at="2026-08-10T00:00:00+08:00"),
            _post("weibo", 2, published_at="2026-08-18T00:00:00+08:00"),
        ],
    )
    newest = await social.list_posts_page(case.id, sort_order="newest")
    oldest = await social.list_posts_page(case.id, sort_order="oldest")
    assert newest[0].native_id == "weibo-2"
    assert oldest[0].native_id == "weibo-1"
    await db.dispose()


async def test_r07_posts_exact_lookup_case_scoped() -> None:
    db, case, _, social, app_repo, *_ = await _setup()
    other = await app_repo.create_case(
        CreateCaseRequest(topic="其它", platforms=["weibo"])
    )
    await social.persist_batch(case_id=case.id, posts=[_post("weibo", 1)])
    await social.persist_batch(case_id=other.id, posts=[_post("weibo", 7)])
    own = await social.list_posts_page(case.id, limit=1)
    assert own
    found = await social.get_post_for_case(case.id, post_id=own[0].id)
    assert found is not None
    # 其它 case 的 post_id 不得返回
    other_post = await social.list_posts_page(other.id, limit=1)
    assert await social.get_post_for_case(case.id, post_id=other_post[0].id) is None
    await db.dispose()


async def test_r08_comments_query_joins_sourcepost_case_scope() -> None:
    db, case, _, social, app_repo, *_ = await _setup()
    other = await app_repo.create_case(
        CreateCaseRequest(topic="其它", platforms=["weibo"])
    )
    await social.persist_batch(
        case_id=case.id,
        posts=[_post("weibo", 1, comments=[_comment(1)])],
    )
    await social.persist_batch(
        case_id=other.id,
        posts=[_post("weibo", 9, comments=[_comment(9)])],
    )
    assert await social.count_comments(case.id) == 1
    comments = await social.list_comments_page(case.id)
    assert len(comments) == 1
    await db.dispose()


async def test_r09_comments_foreign_post_cannot_leak() -> None:
    db, case, _, social, app_repo, *_ = await _setup()
    other = await app_repo.create_case(
        CreateCaseRequest(topic="其它", platforms=["weibo"])
    )
    await social.persist_batch(
        case_id=other.id,
        posts=[_post("weibo", 9, comments=[_comment(9)])],
    )
    other_post = await social.list_posts_page(other.id, limit=1)
    # 用其它 case 的 post_id 查当前 case 的评论 → 0（JOIN SourcePost case scope）
    assert await social.count_comments(case.id, post_id=other_post[0].id) == 0
    assert await social.list_comments_page(case.id, post_id=other_post[0].id) == []
    await db.dispose()


async def test_r10_r11_pagination_deterministic() -> None:
    db, case, _, social, *_ = await _setup()
    posts = [
        _post("weibo", i, published_at="2026-08-15T10:00:00+08:00")
        for i in range(5)
    ]
    await social.persist_batch(case_id=case.id, posts=posts)
    page1 = await social.list_posts_page(case.id, limit=2, offset=0)
    page2 = await social.list_posts_page(case.id, limit=2, offset=2)
    page3 = await social.list_posts_page(case.id, limit=2, offset=4)
    ids = [p.id for p in page1 + page2 + page3]
    assert len(ids) == 5
    assert len(set(ids)) == 5  # 确定性分页：无重复
    await db.dispose()


async def test_r12_r14_aggregate_by_platform_and_content_type() -> None:
    db, case, _, social, *_ = await _setup()
    await social.persist_batch(
        case_id=case.id,
        posts=[
            _post("weibo", 1),
            _post("weibo", 2),
            _post("zhihu", 1),
        ],
    )
    by_platform = dict(await social.count_posts_by_platform(case.id))
    assert by_platform == {"weibo": 2, "zhihu": 1}
    by_type = dict(await social.count_posts_by_content_type(case.id))
    assert by_type.get("post") == 3
    await db.dispose()


async def test_r13_aggregate_by_day() -> None:
    db, case, _, social, *_ = await _setup()
    await social.persist_batch(
        case_id=case.id,
        posts=[
            _post("weibo", 1, published_at="2026-08-10T00:00:00+08:00"),
            _post("weibo", 2, published_at="2026-08-10T23:00:00+08:00"),
            _post("weibo", 3, published_at="2026-08-12T00:00:00+08:00"),
        ],
    )
    rows = await social.list_post_time_rows(case.id)
    assert len(rows) == 3
    await db.dispose()


async def test_r15_empty_result() -> None:
    db, case, _, social, *_ = await _setup()
    assert await social.count_posts(case.id) == 0
    assert await social.list_posts_page(case.id) == []
    assert await social.count_comments(case.id) == 0
    await db.dispose()


# ---------------------------------------------------------------------------
# R16/R17: ApplicationRepository counts + review case scope
# ---------------------------------------------------------------------------


async def test_r16_get_case_database_counts() -> None:
    db, case, _, _, app_repo, finding_repo, *_ = await _setup()
    run_id = "run-1"
    await app_repo.create_claim(case_id=case.id, text="主张", created_by_run_id=run_id)
    await app_repo.create_evidence(
        case_id=case.id, source_type="post", source_id="p1", stance="support", excerpt="证据"
    )
    await app_repo.create_artifact(
        case_id=case.id, kind="opinion", title="t", data={}, run_id=run_id
    )
    counts = await app_repo.get_case_database_counts(case.id)
    assert counts["claims"] == 1
    assert counts["evidence"] == 1
    assert counts["artifacts"] == 1
    assert counts["review_items"] == 0
    assert counts["review_decisions"] == 0
    await db.dispose()


async def test_r17_review_decisions_count_joins_review_item_case_scope() -> None:
    db, case, _, _, app_repo, *_ = await _setup()
    other = await app_repo.create_case(
        CreateCaseRequest(topic="其它", platforms=["weibo"])
    )
    item_own = ReviewItemRecord(
        case_id=case.id, object_type="claim", object_id="c1", summary="s"
    )
    item_other = ReviewItemRecord(
        case_id=other.id, object_type="claim", object_id="c9", summary="s"
    )
    own = await app_repo.create_review_item(item_own)
    other_item = await app_repo.create_review_item(item_other)
    await app_repo.add_review_decision(
        ReviewDecisionRecord(item_id=own.id, decision="approved")
    )
    await app_repo.add_review_decision(
        ReviewDecisionRecord(item_id=other_item.id, decision="approved")
    )
    counts = await app_repo.get_case_database_counts(case.id)
    assert counts["review_items"] == 1
    assert counts["review_decisions"] == 1  # 其它 case 的 decision 不泄漏
    await db.dispose()


# ---------------------------------------------------------------------------
# R18–R20: Finding / Report / CollectionRun
# ---------------------------------------------------------------------------


async def test_r18_finding_repository_query_offset_count() -> None:
    db, case, _, _, _, finding_repo, *_ = await _setup()
    for i in range(3):
        await finding_repo.create(
            FindingRecord(
                case_id=case.id,
                kind="fact_check",
                title=f"结论{i}",
                statement=f"声明文本 {i} 竹知了",
                status="candidate",
                source_run_id="run-1",
            )
        )
    await finding_repo.create(
        FindingRecord(
            case_id=case.id,
            kind="fact_check",
            title="已验证结论",
            statement="声明",
            status="verified",
            source_run_id="run-1",
        )
    )
    assert await finding_repo.count(case.id) == 4
    assert await finding_repo.count(case.id, status="verified") == 1
    assert await finding_repo.count(case.id, query="竹知了") == 3
    listed = await finding_repo.list(case.id, status="candidate", limit=2, offset=0)
    assert len(listed) == 2
    assert len(await finding_repo.list(case.id, status="candidate", limit=10, offset=2)) == 1
    await db.dispose()


async def test_r19_report_document_repository_status_offset_count() -> None:
    db, case, _, _, _, _, report_repo, *_ = await _setup()
    for i in range(3):
        await report_repo.create(
            ReportDocumentRecord(
                family_id=f"f{i}",
                case_id=case.id,
                source_artifact_id=f"a{i}",
                status="draft",
                title=f"报告{i}",
                content_json={},
            )
        )
    await report_repo.create(
        ReportDocumentRecord(
            family_id="fp",
            case_id=case.id,
            source_artifact_id="ap",
            status="published",
            title="已发布报告",
            content_json={"executive_summary": "摘要"},
        )
    )
    assert await report_repo.count_for_case(case.id) == 4
    assert await report_repo.count_for_case(case.id, status="published") == 1
    assert len(await report_repo.list_for_case(case.id, status="draft", limit=2)) == 2
    await db.dispose()


async def test_r20_collection_run_repository_count_for_case() -> None:
    db, case, _, _, _, _, _, collection_repo = await _setup()
    for i in range(2):
        await collection_repo.create(
            case_id=case.id,
            request_fingerprint=f"fp-{i}",
            request_json={"phase": "discovery", "platforms": ["weibo"]},
            phase="discovery",
        )
    assert await collection_repo.count_for_case(case.id) == 2
    await db.dispose()


# ---------------------------------------------------------------------------
# Service: DB01 – get_case_data_overview
# ---------------------------------------------------------------------------


async def test_db01_overview_exact_counts() -> None:
    db, case, service, social, app_repo, finding_repo, report_repo, collection_repo = await _setup()
    await social.persist_batch(
        case_id=case.id,
        posts=[
            _post("weibo", 1, comments=[_comment(1)]),
            _post("zhihu", 1),
        ],
    )
    await collection_repo.create(
        case_id=case.id,
        request_fingerprint="fp",
        request_json={"platforms": ["weibo"]},
        phase="discovery",
    )
    await finding_repo.create(
        FindingRecord(
            case_id=case.id, kind="fact_check", title="t", statement="s",
            status="verified", source_run_id="run-1",
        )
    )
    await report_repo.create(
        ReportDocumentRecord(
            family_id="f", case_id=case.id, source_artifact_id="a",
            status="published", title="r", content_json={},
        )
    )
    await app_repo.create_claim(case_id=case.id, text="主张", created_by_run_id="run-1")
    result = await service.get_case_data_overview(case_id=case.id)
    assert result["ok"] is True
    assert result["counts"]["posts"] == 2
    assert result["counts"]["comments"] == 1
    assert result["counts"]["collection_runs"] == 1
    assert result["counts"]["findings"] == 1
    assert result["counts"]["reports"] == 1
    assert result["counts"]["claims"] == 1
    by_platform = {b["platform"]: b["count"] for b in result["posts_by_platform"]}
    assert by_platform == {"weibo": 1, "zhihu": 1}
    assert result["latest_post_published_at"] is not None
    await db.dispose()


async def test_db01_empty_case_is_not_error() -> None:
    db, case, service, *_ = await _setup()
    result = await service.get_case_data_overview(case_id=case.id)
    assert result["ok"] is True
    assert result["counts"]["posts"] == 0
    assert result["posts_by_platform"] == []
    assert result["active_collection_runs"] == []
    await db.dispose()


async def test_db01_unknown_case_error() -> None:
    db, _, service, *_ = await _setup()
    result = await service.get_case_data_overview(case_id="missing")
    assert result["ok"] is False
    assert result["error"]["code"] == "database_record_not_found"
    await db.dispose()


# ---------------------------------------------------------------------------
# Service: DB02 – query_social_posts
# ---------------------------------------------------------------------------


async def test_db02_posts_pagination_and_next_offset() -> None:
    db, case, service, social, *_ = await _setup()
    await social.persist_batch(
        case_id=case.id, posts=[_post("zhihu", i) for i in range(5)]
    )
    page = await service.query_social_posts(
        case_id=case.id, platforms=["zhihu"], limit=2, offset=0
    )
    assert page["ok"] is True
    assert page["matched_count"] == 5
    assert page["returned_count"] == 2
    assert page["next_offset"] == 2
    last = await service.query_social_posts(
        case_id=case.id, platforms=["zhihu"], limit=2, offset=4
    )
    assert last["next_offset"] is None
    await db.dispose()


async def test_db02_content_bounded() -> None:
    db, case, service, social, *_ = await _setup()
    long_text = "长" * 5000
    await social.persist_batch(case_id=case.id, posts=[_post("weibo", 1, content=long_text)])
    page = await service.query_social_posts(case_id=case.id, limit=10)
    assert len(page["posts"][0]["content"]) <= 3000
    assert page["posts"][0].get("content_truncated") is True
    await db.dispose()


# ---------------------------------------------------------------------------
# Service: DB03 – get_social_post
# ---------------------------------------------------------------------------


async def test_db03_get_social_post_found_and_not_found() -> None:
    db, case, service, social, *_ = await _setup()
    await social.persist_batch(
        case_id=case.id,
        posts=[_post("weibo", 1, comments=[_comment(1), _comment(2)])],
    )
    post = (await service.query_social_posts(case_id=case.id, limit=1))["posts"][0]
    result = await service.get_social_post(case_id=case.id, post_id=post["id"])
    assert result["ok"] is True and result["found"] is True
    assert result["post"]["comment_count"] == 2
    missing = await service.get_social_post(case_id=case.id, post_id="nope")
    assert missing["found"] is False and missing["post"] is None
    await db.dispose()


async def test_db03_foreign_post_not_leaked() -> None:
    db, case, service, social, app_repo, *_ = await _setup()
    other = await app_repo.create_case(
        CreateCaseRequest(topic="其它", platforms=["weibo"])
    )
    await social.persist_batch(case_id=other.id, posts=[_post("weibo", 9)])
    foreign = (await service.query_social_posts(case_id=other.id, limit=1))["posts"][0]
    result = await service.get_social_post(case_id=case.id, post_id=foreign["id"])
    assert result["ok"] is True and result["found"] is False
    await db.dispose()


# ---------------------------------------------------------------------------
# Service: DB04 – query_social_comments
# ---------------------------------------------------------------------------


async def test_db04_comments_by_post() -> None:
    db, case, service, social, *_ = await _setup()
    await social.persist_batch(
        case_id=case.id,
        posts=[_post("weibo", 1, comments=[_comment(1), _comment(2)])],
    )
    post = (await service.query_social_posts(case_id=case.id, limit=1))["posts"][0]
    result = await service.query_social_comments(
        case_id=case.id, post_id=post["id"], limit=10
    )
    assert result["ok"] is True
    assert result["matched_count"] == 2
    assert len(result["comments"]) == 2
    assert "raw_payload" not in result["comments"][0]
    await db.dispose()


# ---------------------------------------------------------------------------
# Service: DB05 – aggregate_social_data
# ---------------------------------------------------------------------------


async def test_db05_aggregate_platform_day_content_type() -> None:
    db, case, service, social, *_ = await _setup()
    await social.persist_batch(
        case_id=case.id,
        posts=[
            _post("weibo", 1, published_at="2026-08-10T00:00:00+00:00"),
            _post("weibo", 2, published_at="2026-08-10T23:00:00+00:00"),
            _post("zhihu", 1, published_at="2026-08-12T00:00:00+00:00"),
        ],
    )
    by_platform = await service.aggregate_social_data(case_id=case.id, group_by="platform")
    assert by_platform["total"] == 3
    assert {b["key"]: b["count"] for b in by_platform["buckets"]} == {
        "weibo": 2,
        "zhihu": 1,
    }
    by_day = await service.aggregate_social_data(case_id=case.id, group_by="day")
    assert {b["key"]: b["count"] for b in by_day["buckets"]}["2026-08-10"] == 2
    by_type = await service.aggregate_social_data(case_id=case.id, group_by="content_type")
    assert {b["key"]: b["count"] for b in by_type["buckets"]}.get("post") == 3
    await db.dispose()


# ---------------------------------------------------------------------------
# Service: DB06 – query_findings
# ---------------------------------------------------------------------------


async def test_db06_findings_status_filter_and_exact_links() -> None:
    db, case, service, _, _, finding_repo, *_ = await _setup()
    await finding_repo.create(
        FindingRecord(
            case_id=case.id, kind="fact_check", title="candidate", statement="s",
            status="candidate", source_run_id="run-1",
        )
    )
    verified = await finding_repo.create(
        FindingRecord(
            case_id=case.id, kind="fact_check", title="verified", statement="s",
            status="verified", source_run_id="run-1",
        )
    )
    await finding_repo.add_evidence_link(verified.id, "ev-1", "supports")
    result = await service.query_findings(case_id=case.id, status="verified")
    assert result["ok"] is True
    assert result["matched_count"] == 1
    assert result["findings"][0]["status"] == "verified"
    exact = await service.query_findings(case_id=case.id, finding_id=verified.id)
    assert exact["found"] is True
    assert exact["findings"][0]["evidence_links"][0]["evidence_ref"] == "ev-1"
    missing = await service.query_findings(case_id=case.id, finding_id="nope")
    assert missing["found"] is False
    await db.dispose()


# ---------------------------------------------------------------------------
# Service: DB07 – query_review_items
# ---------------------------------------------------------------------------


async def test_db07_review_items_exact_with_latest_decision() -> None:
    db, case, service, _, app_repo, *_ = await _setup()
    item = await app_repo.create_review_item(
        ReviewItemRecord(
            case_id=case.id, object_type="claim", object_id="c1",
            status="in_review", current_version=3, summary="s",
        )
    )
    await app_repo.add_review_decision(
        ReviewDecisionRecord(item_id=item.id, decision="approved", actor="tester")
    )
    result = await service.query_review_items(
        case_id=case.id, review_item_id=item.id
    )
    assert result["ok"] is True and result["found"] is True
    assert result["review_items"][0]["current_version"] == 3
    assert result["review_items"][0]["latest_decision"]["decision"] == "approved"
    missing = await service.query_review_items(case_id=case.id, review_item_id="nope")
    assert missing["found"] is False
    await db.dispose()


# ---------------------------------------------------------------------------
# Service: DB08 – query_reports
# ---------------------------------------------------------------------------


async def test_db08_reports_status_filter_and_preview() -> None:
    db, case, service, _, _, _, report_repo, *_ = await _setup()
    await report_repo.create(
        ReportDocumentRecord(
            family_id="f1", case_id=case.id, source_artifact_id="a1",
            status="draft", title="草稿", content_json={},
        )
    )
    await report_repo.create(
        ReportDocumentRecord(
            family_id="f2", case_id=case.id, source_artifact_id="a2",
            status="published", title="已发布", content_json={
                "executive_summary": "摘要",
                "sections": [{"title": "第一节"}],
                "citation_links": ["ev-1"],
            },
        )
    )
    result = await service.query_reports(case_id=case.id, status="published")
    assert result["matched_count"] == 1
    detail = await service.query_reports(
        case_id=case.id, report_id=result["reports"][0]["id"], include_content_preview=True
    )
    assert detail["found"] is True
    preview = detail["reports"][0]["content_preview"]
    assert preview["executive_summary"] == "摘要"
    assert preview["section_titles"] == ["第一节"]
    assert preview["citation_count"] == 1
    await db.dispose()


# ---------------------------------------------------------------------------
# Service: DB09 – query_case_activity
# ---------------------------------------------------------------------------


async def test_db09_case_activity_filter() -> None:
    db, case, service, _, app_repo, *_ = await _setup()
    await app_repo.add_activity_log(
        CaseActivityLogRecord(
            case_id=case.id, activity_type="case_created", summary="创建", actor="tester"
        )
    )
    await app_repo.add_activity_log(
        CaseActivityLogRecord(
            case_id=case.id, activity_type="collection_started", summary="采集", actor="system"
        )
    )
    result = await service.query_case_activity(case_id=case.id)
    assert result["ok"] is True
    assert result["returned_count"] == 2
    filtered = await service.query_case_activity(case_id=case.id, activity_type="collection_started")
    assert filtered["returned_count"] == 1
    assert "metadata_json" not in result["items"][0]
    await db.dispose()


# ---------------------------------------------------------------------------
# Freshness（文档 §90）：新增数据后立即查询可见（cache=0 语义）
# ---------------------------------------------------------------------------


async def test_freshness_new_post_visible_immediately() -> None:
    db, case, service, social, *_ = await _setup()
    await social.persist_batch(case_id=case.id, posts=[_post("weibo", 1)])
    first = await service.get_case_data_overview(case_id=case.id)
    assert first["counts"]["posts"] == 1
    await social.persist_batch(case_id=case.id, posts=[_post("weibo", 2)])
    second = await service.get_case_data_overview(case_id=case.id)
    assert second["counts"]["posts"] == 2
    await db.dispose()


async def test_freshness_collection_run_partial_persist_visible() -> None:
    db, case, service, _, _, _, _, collection_repo = await _setup()
    await service.get_case_data_overview(case_id=case.id)
    run = await collection_repo.create(
        case_id=case.id,
        request_fingerprint="fp",
        request_json={"phase": "deep", "platforms": ["zhihu"]},
        phase="deep",
    )
    # 模拟平台完成后的增量计数（CollectionRun 可能持续写库）
    from sqlalchemy import select

    from app.infrastructure.database.models import CollectionRunRecord

    async with db.session_factory() as session:
        record = await session.scalar(
            select(CollectionRunRecord).where(CollectionRunRecord.id == run.id)
        )
        assert record is not None
        record.posts_collected = 47
        record.comments_collected = 3
        await session.commit()
    result = await service.get_case_data_overview(case_id=case.id)
    active = result["active_collection_runs"]
    assert len(active) == 1
    assert active[0]["posts_collected"] == 47
    await db.dispose()
