from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.application.ports.crawler import CrawlRequest
from app.application.repositories import ApplicationRepository
from app.core.config import get_settings
from app.infrastructure.crawler import MediaCrawlerAdapter, MediaCrawlerConfig
from app.infrastructure.database import Database
from app.infrastructure.database.social_repository import SocialRepository
from app.schemas.cases import CreateCaseRequest

PLATFORMS = ("weibo", "bilibili", "tieba", "zhihu", "douyin")


async def run(
    platform: str,
    topic: str,
    limit: int,
    max_comments: int,
    timeout_seconds: float,
) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    cases = ApplicationRepository(database)
    social = SocialRepository(database)
    crawler = MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=settings.mediacrawler_root.resolve(),
            output_root=settings.mediacrawler_output_root.resolve(),
            python_executable=(
                settings.mediacrawler_python_executable or Path(sys.executable)
            ).resolve(),
            entrypoint=settings.mediacrawler_entrypoint.resolve(),
            login_type=settings.mediacrawler_login_type,
            headless=settings.mediacrawler_headless,
            include_comments=settings.mediacrawler_include_comments,
            max_comments_per_post=max_comments,
            timeout_seconds=timeout_seconds,
            max_output_runs=settings.mediacrawler_max_output_runs,
            usage_mode=settings.mediacrawler_usage_mode,
            weibo_cookies=settings.mediacrawler_weibo_cookies.get_secret_value(),
            bilibili_cookies=(
                settings.mediacrawler_bilibili_cookies.get_secret_value()
            ),
            tieba_cookies=settings.mediacrawler_tieba_cookies.get_secret_value(),
            zhihu_cookies=settings.mediacrawler_zhihu_cookies.get_secret_value(),
            douyin_cookies=settings.mediacrawler_douyin_cookies.get_secret_value(),
        )
    )
    case = await cases.create_case(
        CreateCaseRequest(
            title=f"{platform} real crawl smoke test",
            topic=topic,
            description="Real platform readiness validation.",
            platforms=[platform],
        )
    )
    try:
        posts = await crawler.collect(
            CrawlRequest(
                topic=topic,
                platforms=[platform],
                time_range={"start": None, "end": None},
                limit_per_platform=limit,
            )
        )
        first = await social.persist_batch(case_id=case.id, posts=posts)
        second = await social.persist_batch(case_id=case.id, posts=posts)
        comment_count = sum(
            len(comments)
            for post in posts
            if isinstance((comments := post.get("comments")), list)
        )
        required_fields = {
            "native_id",
            "platform",
            "content_type",
            "content",
            "published_at",
            "metrics",
            "raw",
        }
        checks: dict[str, object] = {
            "case_id": case.id,
            "real_search": bool(posts),
            "post_count": len(posts),
            "comment_count": comment_count,
            "normalized_schema": all(
                required_fields.issubset(post) for post in posts
            ),
            "raw_persisted": first.raw_records_created
            >= len(posts) + comment_count,
            "normalized_persisted": first.posts_created == len(posts),
            "repeat_deduplicated": (
                second.posts_created == 0
                and second.comments_created == 0
                and second.raw_records_created == 0
            ),
            "comments_verified": (
                comment_count > 0
                if settings.mediacrawler_include_comments
                else "disabled"
            ),
        }
        ready = all(
            value is True
            for key, value in checks.items()
            if key
            in {
                "real_search",
                "normalized_schema",
                "raw_persisted",
                "normalized_persisted",
                "repeat_deduplicated",
                "comments_verified",
            }
        )
        status = "ready" if ready else "validation_failed"
        await social.set_platform_capability(
            platform,
            status=status,
            checks=checks,
            last_error=None if ready else "One or more readiness checks failed",
        )
        print(
            json.dumps(
                {"platform": platform, "status": status, "checks": checks},
                ensure_ascii=False,
                indent=2,
            )
        )
        if not ready:
            raise RuntimeError(f"{platform} readiness checks failed")
    except Exception as exc:
        await social.set_platform_capability(
            platform,
            status="validation_failed",
            checks={"case_id": case.id},
            last_error=str(exc),
        )
        raise
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("platform", choices=PLATFORMS)
    parser.add_argument("--topic", default="新能源汽车")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--max-comments", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    settings = get_settings()
    if settings.demo_mode:
        raise SystemExit(
            "Refuse to mark a platform ready under DEMO_MODE=true. "
            "Set DEMO_MODE=false and provide real cookies before P1-2.1."
        )
    asyncio.run(
        run(
            args.platform,
            args.topic,
            max(1, min(args.limit, 10)),
            max(1, min(args.max_comments, 20)),
            max(30, min(args.timeout, 1_800)),
        )
    )


if __name__ == "__main__":
    main()
