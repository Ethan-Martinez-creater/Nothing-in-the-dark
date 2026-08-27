"""External tool handlers executed inside the sandbox worker (15).

这些函数只能通过 :mod:`app.harness.sandbox_worker` 子进程运行——父进程
不直接调用，避免工具处理器在主进程内自由访问文件/网络/环境变量。注册表
按工具名映射；payload 必须是可 JSON 序列化的 dict。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)


def _build_crawler() -> Any:
    """按子进程环境构建爬虫适配器（demo 或 MediaCrawler）。"""
    demo = str(os.environ.get("COIFESP_DEMO_MODE", "")).lower()
    if demo in {"1", "true", "yes"}:
        from app.infrastructure.crawler.demo import DemoCrawlerAdapter

        return DemoCrawlerAdapter()
    from pathlib import Path

    from app.infrastructure.crawler import (
        MediaCrawlerAdapter,
        MediaCrawlerConfig,
    )

    def _env(name: str) -> str:
        return os.environ.get(name, "")

    return MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=Path(_env("COIFESP_MEDIACRAWLER_ROOT") or "."),
            output_root=Path(_env("COIFESP_MEDIACRAWLER_OUTPUT_ROOT") or "."),
            python_executable=Path(
                _env("COIFESP_MEDIACRAWLER_PYTHON_EXECUTABLE") or sys.executable
            ),
            entrypoint=(
                Path(_env("COIFESP_MEDIACRAWLER_ENTRYPOINT"))
                if _env("COIFESP_MEDIACRAWLER_ENTRYPOINT")
                else None
            ),
            login_type=_env("COIFESP_MEDIACRAWLER_LOGIN_TYPE") or "qrcode",
            headless=_env("COIFESP_MEDIACRAWLER_HEADLESS") != "false",
            include_comments=_env("COIFESP_MEDIACRAWLER_INCLUDE_COMMENTS") == "1",
            max_comments_per_post=int(_env("COIFESP_MEDIACRAWLER_MAX_COMMENTS_PER_POST") or 0),
            timeout_seconds=int(_env("COIFESP_MEDIACRAWLER_TIMEOUT_SECONDS") or 120),
            max_output_runs=int(_env("COIFESP_MEDIACRAWLER_MAX_OUTPUT_RUNS") or 1),
            usage_mode=_env("COIFESP_MEDIACRAWLER_USAGE_MODE") or "acquire",
            weibo_cookies=_env("COIFESP_MEDIACRAWLER_WEIBO_COOKIES"),
            bilibili_cookies=_env("COIFESP_MEDIACRAWLER_BILIBILI_COOKIES"),
            tieba_cookies=_env("COIFESP_MEDIACRAWLER_TIEBA_COOKIES"),
            zhihu_cookies=_env("COIFESP_MEDIACRAWLER_ZHIHU_COOKIES"),
            douyin_cookies=_env("COIFESP_MEDIACRAWLER_DOUYIN_COOKIES"),
        )
    )


async def collect_social_posts(payload: dict[str, Any]) -> dict[str, Any]:
    """子进程内执行平台采集（仅外部副作用段；持久化由父进程完成）。"""
    from app.application.ports.crawler import CrawlRequest

    request = CrawlRequest(
        topic=str(payload.get("topic") or ""),
        platforms=list(payload.get("platforms") or []),
        time_range=dict(payload.get("time_range") or {}),
        limit_per_platform=int(payload.get("limit_per_platform") or 150),
        per_day_limit=int(payload.get("per_day_limit") or 150),
        comment_limit=int(payload.get("comment_limit") or 10),
        keywords=dict(payload.get("keywords") or {}),
    )
    crawler = _build_crawler()
    posts = await crawler.collect(request)
    return {
        "ok": True,
        "posts": list(posts),
        "platforms": list(request.platforms),
    }


async def echo(payload: dict[str, Any]) -> dict[str, Any]:
    """测试桩：回显 payload，用于验证子进程执行/秘密注入/环境隔离。"""
    leaked = {key: value for key, value in os.environ.items() if "SECRET" in key.upper()}
    return {
        "ok": True,
        "echo": payload,
        "cwd": os.getcwd(),
        "leaked_env_secrets": list(leaked.keys()),
        "proxy": os.environ.get("HTTPS_PROXY", ""),
    }


REGISTRY: dict[str, Any] = {
    "collect_social_posts": collect_social_posts,
    "echo": echo,
}


def run_external(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    handler = REGISTRY.get(tool_name)
    if handler is None:
        return {
            "ok": False,
            "error": {
                "code": "external_tool_not_found",
                "message": f"external tool {tool_name!r} is not registered",
            },
        }
    try:
        import asyncio

        return asyncio.run(handler(payload))
    except Exception as exc:  # noqa: BLE001 - 子进程边界，异常转 JSON
        logger.exception("external tool %s failed", tool_name)
        return {
            "ok": False,
            "error": {
                "code": "external_tool_failed",
                "message": f"{type(exc).__name__}: {exc}"[:500],
            },
        }
