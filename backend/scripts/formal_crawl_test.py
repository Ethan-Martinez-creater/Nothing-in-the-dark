"""Direct, reproducible collection test without starting API or frontend services."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.application.ports.crawler import CrawlRequest
from app.core.config import get_settings
from app.infrastructure.crawler import MediaCrawlerAdapter, MediaCrawlerConfig
from app.infrastructure.crawler.mediacrawler import _within_time_range
from app.services.crawl_coverage import apply_coverage
from app.services.media_features import media_items_from_post

PLATFORMS = ("weibo", "bilibili", "tieba", "zhihu", "douyin")
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m3u8", ".flv"}


def build_adapter(
    timeout_seconds: float,
    max_comments: int,
    *,
    include_comments: bool,
) -> tuple[MediaCrawlerAdapter, Any]:
    settings = get_settings()
    config = MediaCrawlerConfig(
        root=settings.mediacrawler_root.resolve(),
        output_root=settings.mediacrawler_output_root.resolve(),
        python_executable=(
            settings.mediacrawler_python_executable or Path(sys.executable)
        ).resolve(),
        entrypoint=settings.mediacrawler_entrypoint.resolve(),
        login_type=settings.mediacrawler_login_type,
        headless=settings.mediacrawler_headless,
        include_comments=include_comments,
        max_comments_per_post=max_comments,
        timeout_seconds=timeout_seconds,
        max_output_runs=settings.mediacrawler_max_output_runs,
        usage_mode=settings.mediacrawler_usage_mode,
        weibo_cookies=settings.mediacrawler_weibo_cookies.get_secret_value(),
        bilibili_cookies=settings.mediacrawler_bilibili_cookies.get_secret_value(),
        tieba_cookies=settings.mediacrawler_tieba_cookies.get_secret_value(),
        zhihu_cookies=settings.mediacrawler_zhihu_cookies.get_secret_value(),
        douyin_cookies=settings.mediacrawler_douyin_cookies.get_secret_value(),
    )
    return MediaCrawlerAdapter(config), settings


def _new_run(output_root: Path, before: set[Path]) -> Path | None:
    created = [path for path in output_root.iterdir() if path.is_dir() and path not in before]
    return max(created, key=lambda path: path.stat().st_mtime, default=None)


def _downloaded_media(run_root: Path | None) -> dict[str, Any]:
    if run_root is None:
        return {"image_files": 0, "video_files": 0, "other_files": 0, "bytes": 0}
    files = [path for path in run_root.rglob("*") if path.is_file()]
    images = [path for path in files if path.suffix.lower() in _IMAGE_SUFFIXES]
    videos = [path for path in files if path.suffix.lower() in _VIDEO_SUFFIXES]
    payload = [path for path in files if path.suffix.lower() != ".jsonl"]
    return {
        "image_files": len(images),
        "video_files": len(videos),
        "other_files": len(payload) - len(images) - len(videos),
        "bytes": sum(path.stat().st_size for path in payload),
    }


def _summarize_posts(
    posts: list[dict[str, Any]], request: CrawlRequest, target: int
) -> dict[str, Any]:
    coverage = apply_coverage(posts, request) if posts else None
    media = [item for post in posts for item in media_items_from_post(post)]
    dates = Counter(str(post.get("published_at") or "")[:10] or "unknown" for post in posts)
    comment_count = sum(
        len(comments)
        for post in posts
        if isinstance((comments := post.get("comments")), list)
    )
    raw_records = [
        post.get("raw") if isinstance(post.get("raw"), dict) else {} for post in posts
    ]
    return {
        "raw_in_range_count": len(posts),
        "coverage_kept_count": len(coverage.posts) if coverage else 0,
        "target_count": target,
        "target_met_before_quality_filter": len(posts) >= target,
        "target_met_after_quality_filter": bool(coverage and len(coverage.posts) >= target),
        "date_counts": dict(sorted(dates.items())),
        "comment_count": comment_count,
        "posts_with_comments": sum(bool(post.get("comments")) for post in posts),
        "media_url_count": len(media),
        "posts_with_media_urls": sum(bool(media_items_from_post(post)) for post in posts),
        "image_url_count": sum(item.get("media_type") == "image" for item in media),
        "video_url_count": sum(item.get("media_type") == "video" for item in media),
        "raw_cover_url_count": sum(bool(raw.get("cover_url")) for raw in raw_records),
        "raw_video_download_url_count": sum(
            bool(raw.get("video_download_url")) for raw in raw_records
        ),
        "raw_note_download_url_count": sum(
            bool(raw.get("note_download_url")) for raw in raw_records
        ),
        "content_types": dict(Counter(str(post.get("content_type")) for post in posts)),
        "coverage": asdict(coverage.stats) if coverage else None,
    }


async def run(args: argparse.Namespace) -> int:
    include_comments = settings_include_comments = get_settings().mediacrawler_include_comments
    if args.no_comments:
        include_comments = False
    adapter, settings = build_adapter(
        args.timeout,
        args.max_comments,
        include_comments=include_comments,
    )
    output_root = settings.mediacrawler_output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    before = {path for path in output_root.iterdir() if path.is_dir()}
    cookies = {
        "weibo": settings.mediacrawler_weibo_cookies,
        "bilibili": settings.mediacrawler_bilibili_cookies,
        "tieba": settings.mediacrawler_tieba_cookies,
        "zhihu": settings.mediacrawler_zhihu_cookies,
        "douyin": settings.mediacrawler_douyin_cookies,
    }
    keywords = args.keyword or [args.topic]
    request = CrawlRequest(
        topic=args.topic,
        platforms=[args.platform],
        time_range={"start": args.start, "end": args.end},
        limit_per_platform=args.limit,
        per_day_limit=args.per_day,
        comment_limit=args.max_comments,
        keywords={args.platform: keywords},
    )
    posts: list[dict[str, Any]] = []
    error: str | None = None
    run_root = (
        Path(args.existing_run).resolve()  # noqa: ASYNC240 - diagnostic CLI setup
        if args.existing_run
        else None
    )
    if run_root is None:
        try:
            posts = await adapter.collect(request)
        except Exception as exc:  # diagnostic runner must still report partial output
            error = f"{type(exc).__name__}: {exc}"
        run_root = _new_run(output_root, before)
    all_upstream: list[dict[str, Any]] = []
    if run_root is not None:
        platform_root = run_root / args.platform
        if platform_root.exists():
            all_upstream = adapter._load_platform_posts(args.platform, platform_root)
    in_range = [
        post
        for post in all_upstream
        if _within_time_range(post.get("published_at"), request.time_range)
    ]
    if posts:
        in_range = posts
    else:
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for post in in_range:
            key = (str(post.get("platform")), str(post.get("native_id")))
            unique.setdefault(key, post)
        in_range = list(unique.values())

    summary = {
        "platform": args.platform,
        "topic": args.topic,
        "keywords": keywords,
        "window": request.time_range,
        "status": "analyzed" if args.existing_run else (
            "completed" if error is None else "failed"
        ),
        "error": error[-3000:] if error else None,
        "run_root": str(run_root) if run_root else None,
        "preflight": {
            "demo_mode": settings.demo_mode,
            "login_type": settings.mediacrawler_login_type,
            "headless": settings.mediacrawler_headless,
            "comments_configured": settings_include_comments,
            "comments_enabled_for_run": include_comments,
            "cookie_configured": bool(cookies[args.platform].get_secret_value()),
            "binary_media_download_enabled": False,
        },
        "upstream_count_before_time_filter": len(all_upstream),
        **_summarize_posts(in_range, request, args.limit),
        "downloaded_media": _downloaded_media(run_root),
    }
    print("\nCOIFESP_FORMAL_CRAWL_RESULT")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if error is None else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("platform", choices=PLATFORMS)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--keyword", action="append", help="repeat for each query")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--per-day", type=int, default=10)
    parser.add_argument("--max-comments", type=int, default=10)
    parser.add_argument("--no-comments", action="store_true")
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--existing-run", help="analyze an existing run without network")
    args = parser.parse_args()
    if args.limit < 1 or args.per_day < 1 or args.max_comments < 1:
        raise SystemExit("limits must be positive")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
