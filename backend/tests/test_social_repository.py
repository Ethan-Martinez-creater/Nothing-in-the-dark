from pathlib import Path

from sqlalchemy import func, select

from app.application.repositories import ApplicationRepository
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    RawSocialRecord,
    SourceCommentRecord,
    SourcePostRecord,
)
from app.infrastructure.database.social_repository import SocialRepository
from app.schemas.cases import CreateCaseRequest


async def test_social_persistence_keeps_raw_and_deduplicates_normalized(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'social.db'}")
    await database.create_schema()
    cases = ApplicationRepository(database)
    repository = SocialRepository(database)
    case = await cases.create_case(
        CreateCaseRequest(topic="测试采集", platforms=["weibo"])
    )
    posts: list[dict[str, object]] = [
        {
            "id": "weibo-p1",
            "native_id": "p1",
            "platform": "weibo",
            "content_type": "post",
            "title": "",
            "content": "第一版内容",
            "author": "作者",
            "published_at": "2026-07-30T10:00:00+08:00",
            "engagement": 5,
            "metrics": {"liked_count": 5},
            "url": "https://weibo.example/p1",
            "raw": {"note_id": "p1", "content": "第一版内容"},
            "comments": [
                {
                    "native_id": "c1",
                    "parent_native_id": None,
                    "content": "评论",
                    "author_id": "hash-user",
                    "author_name": "用户",
                    "published_at": "2026-07-30T10:01:00+08:00",
                    "metrics": {"like_count": 1},
                    "raw": {"comment_id": "c1", "content": "评论"},
                }
            ],
        }
    ]

    first = await repository.persist_batch(case_id=case.id, posts=posts)
    second = await repository.persist_batch(case_id=case.id, posts=posts)

    async with database.session_factory() as session:
        post_count = await session.scalar(
            select(func.count()).select_from(SourcePostRecord)
        )
        comment_count = await session.scalar(
            select(func.count()).select_from(SourceCommentRecord)
        )
        raw_count = await session.scalar(
            select(func.count()).select_from(RawSocialRecord)
        )

    assert first.posts_created == 1
    assert first.comments_created == 1
    assert first.raw_records_created == 2
    assert second.posts_updated == 1
    assert second.comments_updated == 1
    assert second.raw_records_created == 0
    assert post_count == 1
    assert comment_count == 1
    assert raw_count == 2
    await database.dispose()
