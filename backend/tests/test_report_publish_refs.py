"""报告发布引用校验回归（内存库）。

覆盖报告生成器产出的四种引用形态都能通过 publish gate：
social_post:<短id>、social_comment:<短id>、裸 uuid（帖子/评论完整 id）、
aggregate_social_data:<group_by> 聚合引用；缺失引用与跨 case 引用仍被阻止。
"""

from __future__ import annotations

import uuid
from typing import Any

from app.application.report_document_service import ReportDocumentService
from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.infrastructure.database.models import ReportDocumentRecord
from app.infrastructure.database.social_repository import SocialRepository
from app.schemas.cases import CreateCaseRequest
from tests.memory_db import MemoryDatabase


def _post(platform: str, index: int, comments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "platform": platform,
        "native_id": f"{platform}-{index}",
        "content_type": "post",
        "title": "",
        "content": f"{platform} 事件讨论内容 {index}",
        "author": f"author-{platform}",
        "published_at": "2026-08-15T10:00:00+00:00",
        "engagement": 10,
        "metrics": {},
        "url": "u",
        "raw": {},
        "comments": comments or [],
    }


def _comment(index: int) -> dict[str, Any]:
    return {
        "native_id": f"c-{index}",
        "content": f"评论 {index}",
        "author_id": "a",
        "author_name": "n",
        "published_at": "2026-08-16T10:00:00+00:00",
        "metrics": {},
    }


async def _make_report(
    service: ReportDocumentService,
    case_id: str,
    citation_links: list[Any],
) -> str:
    record = ReportDocumentRecord(
        family_id=str(uuid.uuid4()),
        case_id=case_id,
        source_artifact_id=str(uuid.uuid4()),
        status="draft",
        title="调查报告",
        content_json={
            "title": "调查报告",
            "executive_summary": "摘要",
            "sections": [{"title": "背景", "content": "内容"}],
            "citation_links": citation_links,
        },
    )
    return (await service._reports.create(record)).id  # noqa: SLF001


async def _setup():
    db = MemoryDatabase()
    await db.create_schema()
    repo = ApplicationRepository(db)
    case = await repo.create_case(
        CreateCaseRequest(topic="报告案例", platforms=["weibo", "zhihu"])
    )
    social = SocialRepository(db)
    service = ReportDocumentService(db)
    return db, case.id, social, service, repo


async def _publish(
    service: ReportDocumentService, case_id: str, report_id: str
) -> None:
    updated = await service.change_status(case_id, report_id, "published")
    assert updated.status == "published"


async def _assert_publish_fails(
    service: ReportDocumentService, case_id: str, report_id: str, issue: str
) -> None:
    try:
        await service.change_status(case_id, report_id, "published")
        raise AssertionError("expected publish to fail")
    except ApplicationError as exc:
        assert exc.code == "report_publish_validation_failed"
        assert any(issue in str(item) for item in (exc.details or []))


async def test_publish_accepts_social_post_and_comment_and_aggregate_refs() -> None:
    db, case_id, social, service, _ = await _setup()
    await social.persist_batch(
        case_id=case_id,
        posts=[
            _post("weibo", 1, comments=[_comment(1), _comment(2)]),
            _post("zhihu", 2),
        ],
    )
    post = (await social.list_posts_page(case_id, limit=1))[0]
    comment = (await social.list_comments_page(case_id, limit=1))[0]
    report_id = await _make_report(
        service,
        case_id,
        [
            {"conclusion": "帖子", "evidence_ids": [f"social_post:{post.id[:8]}"]},
            {"conclusion": "评论", "evidence_ids": [f"social_comment:{comment.id[:8]}"]},
            {"conclusion": "聚合", "evidence_ids": ["aggregate_social_data:day"]},
        ],
    )
    await _publish(service, case_id, report_id)
    await db.dispose()


async def test_publish_accepts_bare_uuid_post_and_comment() -> None:
    db, case_id, social, service, _ = await _setup()
    await social.persist_batch(
        case_id=case_id,
        posts=[_post("weibo", 1, comments=[_comment(1)])],
    )
    post = (await social.list_posts_page(case_id, limit=1))[0]
    comment = (await social.list_comments_page(case_id, limit=1))[0]
    report_id = await _make_report(
        service,
        case_id,
        [
            {"conclusion": "帖子", "evidence_ids": [post.id]},
            {"conclusion": "评论", "evidence_ids": [comment.id]},
        ],
    )
    await _publish(service, case_id, report_id)
    await db.dispose()


async def test_publish_rejects_missing_post_ref() -> None:
    db, case_id, _, service, _ = await _setup()
    report_id = await _make_report(
        service,
        case_id,
        [{"conclusion": "不存在", "evidence_ids": ["social_post:deadbeef"]}],
    )
    await _assert_publish_fails(service, case_id, report_id, "post_not_found")
    await db.dispose()


async def test_publish_rejects_cross_case_post_ref() -> None:
    db, case_id, social, service, repo = await _setup()
    other = await repo.create_case(
        CreateCaseRequest(topic="其它事件", platforms=["weibo"])
    )
    await social.persist_batch(case_id=other.id, posts=[_post("weibo", 9)])
    foreign_post = (await social.list_posts_page(other.id, limit=1))[0]
    report_id = await _make_report(
        service,
        case_id,
        [{"conclusion": "跨 case", "evidence_ids": [f"social_post:{foreign_post.id[:8]}"]}],
    )
    await _assert_publish_fails(service, case_id, report_id, "post_not_in_case")
    await db.dispose()
