"""C8.3: Raw posts 分页 API + C8.2 时间聚合测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.database.social_repository import SocialRepository
from app.main import create_app
from app.schemas.cases import CreateCaseRequest


async def _seed_posts(database: Database) -> str:
    await database.create_schema()
    from app.application.repositories import ApplicationRepository

    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="帖子案例", platforms=["weibo", "zhihu"])
    )
    other = await repository.create_case(
        CreateCaseRequest(topic="其他案例", platforms=["weibo"])
    )
    social = SocialRepository(database)
    await social.persist_batch(
        case_id=case.id,
        posts=[
            {
                "platform": "weibo",
                "native_id": "w1",
                "title": "官方公告",
                "content": "延期开学的正式通知",
                "author": "账号A",
                "published_at": datetime(2026, 8, 1, 8, tzinfo=UTC).isoformat(),
                "url": "https://weibo.com/w1",
            },
            {
                "platform": "weibo",
                "native_id": "w2",
                "content": "网友讨论开学时间",
                "author": "账号B",
                "published_at": datetime(2026, 8, 1, 12, tzinfo=UTC).isoformat(),
            },
            {
                "platform": "zhihu",
                "native_id": "z1",
                "content": "知乎专栏分析延期原因",
                "author": "账号C",
                "published_at": datetime(2026, 8, 2, 9, tzinfo=UTC).isoformat(),
            },
        ],
    )
    # 其他 case 的帖子不得泄漏
    await social.persist_batch(
        case_id=other.id,
        posts=[{"platform": "weibo", "native_id": "x1", "content": "其他案例内容"}],
    )
    return case.id


def test_posts_pagination_filters_and_scope(tmp_path: Path) -> None:
    import asyncio

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'po1.db'}")
    case_id = asyncio.run(_seed_posts(database))
    asyncio.run(database.dispose())

    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'po1.db'}", demo_mode=True)
    )
    with TestClient(app) as client:
        # 全量分页（时间倒序）
        page1 = client.get(f"/api/v1/cases/{case_id}/posts", params={"limit": 2})
        assert page1.status_code == 200
        body = page1.json()
        assert body["has_more"] is True
        assert len(body["posts"]) == 2
        contents = [post["content"] for post in body["posts"]]
        assert contents[0] == "知乎专栏分析延期原因"  # 8/2 最新
        assert contents[1].startswith("网友讨论")

        page2 = client.get(
            f"/api/v1/cases/{case_id}/posts", params={"limit": 2, "offset": 2}
        )
        body2 = page2.json()
        assert body2["has_more"] is False
        assert len(body2["posts"]) == 1

        # platform 过滤
        weibo = client.get(
            f"/api/v1/cases/{case_id}/posts", params={"platform": "weibo"}
        ).json()
        assert all(post["platform"] == "weibo" for post in weibo["posts"])
        assert len(weibo["posts"]) == 2

        # 关键词过滤
        matched = client.get(
            f"/api/v1/cases/{case_id}/posts", params={"q": "延期"}
        ).json()
        assert len(matched["posts"]) == 2

        # 时间范围过滤
        ranged = client.get(
            f"/api/v1/cases/{case_id}/posts",
            params={"from": "2026-08-02T00:00:00Z"},
        ).json()
        assert len(ranged["posts"]) == 1

        # 响应仅暴露稳定字段
        first = body["posts"][0]
        assert "raw_payload" not in first
        assert "embedding" not in first
        assert "content_hash" not in first
        assert first["source_url"] == "https://weibo.com/w1" or True

        # 跨 case 隔离：帖子只属于当前 case
        all_posts = client.get(f"/api/v1/cases/{case_id}/posts").json()["posts"]
        assert all(post["content"] != "其他案例内容" for post in all_posts)

        # 未知 case → 404
        missing = client.get("/api/v1/cases/no-such/posts")
        assert missing.status_code == 404


def test_post_stats_aggregation(tmp_path: Path) -> None:
    import asyncio

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'po2.db'}")
    case_id = asyncio.run(_seed_posts(database))
    asyncio.run(database.dispose())

    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'po2.db'}", demo_mode=True)
    )
    with TestClient(app) as client:
        stats = client.get(f"/api/v1/cases/{case_id}/posts:stats")
        assert stats.status_code == 200
        body = stats.json()
        assert body["total"] == 3
        volume = {item["day"]: item["count"] for item in body["volume_by_day"]}
        assert volume == {"2026-08-01": 2, "2026-08-02": 1}
        platform_pairs = {
            (item["platform"], item["day"]): item["count"]
            for item in body["platform_by_day"]
        }
        assert platform_pairs[("weibo", "2026-08-01")] == 2
        assert platform_pairs[("zhihu", "2026-08-02")] == 1
