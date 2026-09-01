"""MediaCrawler adapter tests（MC01/MC03/MC04/MC07/MC09）。

覆盖本轮性能改造：一平台一进程多关键词、aggregate upstream cap、
Discovery 真正关闭评论、CrawlRequest legacy defaults 不变、
并发平台输出路径不冲突。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.application.ports.crawler import CrawlRequest
from app.infrastructure.crawler.mediacrawler import (
    MediaCrawlerAdapter,
    MediaCrawlerConfig,
)


def _make_adapter(tmp_path: Path, runner: Any) -> MediaCrawlerAdapter:
    root = tmp_path / "MediaCrawler"
    root.mkdir()
    (root / "main.py").write_text("# stub\n", encoding="utf-8")
    return MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=root,
            output_root=tmp_path / "out",
            python_executable=Path(sys.executable),
            entrypoint=root / "main.py",
            include_comments=True,
            max_comments_per_post=10,
        ),
        command_runner=runner,
    )


def _write_posts(save_path: Path, count: int) -> None:
    save_path.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(count):
        lines.append(
            json.dumps(
                {
                    "id": f"id-{i}",
                    "content": f"竹知了事件相关讨论内容 {i}，足够长以通过过滤",
                    "create_time": "2026-08-15 10:00:00",
                    "nickname": "user",
                },
                ensure_ascii=False,
            )
        )
    (save_path / "contents.jsonl").write_text(
        "\n".join(lines), encoding="utf-8"
    )


async def test_mc01_one_platform_multiple_keywords_one_subprocess(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    async def runner(command, cwd, timeout_seconds):
        calls.append(command)
        save_path = Path(command[command.index("--save_data_path") + 1])
        _write_posts(save_path, 2)
        return 0, "", ""

    adapter = _make_adapter(tmp_path, runner)
    posts = await adapter.collect(
        CrawlRequest(
            topic="竹知了",
            platforms=["weibo"],
            time_range={},
            keywords={"weibo": ["竹知了", "华为 竹知了"]},
        )
    )
    assert len(calls) == 1  # 一次命令，一个 process
    command = calls[0]
    keywords_flag = command[command.index("--keywords") + 1]
    assert keywords_flag == "竹知了,华为 竹知了"
    assert len(posts) == 2


async def test_mc03_discovery_comments_truly_disabled(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    async def runner(command, cwd, timeout_seconds):
        commands.append(command)
        save_path = Path(command[command.index("--save_data_path") + 1])
        _write_posts(save_path, 1)
        return 0, "", ""

    adapter = _make_adapter(tmp_path, runner)
    await adapter.collect(
        CrawlRequest(
            topic="竹知了",
            platforms=["bilibili"],
            time_range={},
            include_comments=False,
            comment_limit=0,
        )
    )
    command = commands[0]
    assert command[command.index("--get_comment") + 1] == "false"
    assert command[command.index("--max_comments_count_singlenotes") + 1] == "0"


async def test_mc04_aggregate_upstream_budget_respected(tmp_path: Path) -> None:
    async def runner(command, cwd, timeout_seconds):
        save_path = Path(command[command.index("--save_data_path") + 1])
        _write_posts(save_path, 8)
        return 0, "", ""

    adapter = _make_adapter(tmp_path, runner)
    posts = await adapter.collect(
        CrawlRequest(
            topic="竹知了",
            platforms=["weibo"],
            time_range={},
            keywords={"weibo": ["竹知了", "华为 竹知了"]},
            upstream_limit_per_platform=5,
        )
    )
    # 平台 aggregate cap：即使上游返回 8 条也截断到 5 条
    assert len(posts) == 5


async def test_mc07_crawlrequest_legacy_defaults_unchanged() -> None:
    request = CrawlRequest(topic="x", platforms=["weibo"], time_range={})
    assert request.limit_per_platform == 150
    assert request.per_day_limit == 150
    assert request.comment_limit == 10
    assert request.upstream_limit_per_platform is None
    assert request.include_comments is None


async def test_mc09_concurrent_platform_output_paths_do_not_collide(
    tmp_path: Path,
) -> None:
    seen_paths: list[Path] = []

    async def runner(command, cwd, timeout_seconds):
        save_path = Path(command[command.index("--save_data_path") + 1])
        seen_paths.append(save_path)
        _write_posts(save_path, 1)
        return 0, "", ""

    adapter = _make_adapter(tmp_path, runner)
    await adapter.collect(
        CrawlRequest(
            topic="竹知了",
            platforms=["weibo", "bilibili"],
            time_range={},
        )
    )
    assert len(seen_paths) == 2
    assert seen_paths[0] != seen_paths[1]
    assert seen_paths[0].name == "weibo"
    assert seen_paths[1].name == "bilibili"


async def test_mc10_nonzero_exit_keeps_partial_data(tmp_path: Path) -> None:
    """INV-4：进程非零退出但已产出部分数据时保留，不丢弃整平台。"""
    async def runner(command, cwd, timeout_seconds):
        save_path = Path(command[command.index("--save_data_path") + 1])
        _write_posts(save_path, 3)
        return 3, "", "crashed after partial write"

    adapter = _make_adapter(tmp_path, runner)
    posts = await adapter.collect(
        CrawlRequest(
            topic="竹知了",
            platforms=["weibo"],
            time_range={},
            upstream_limit_per_platform=10,
        )
    )
    assert len(posts) == 3


async def test_mc11_nonzero_exit_without_data_raises(tmp_path: Path) -> None:
    """进程非零退出且无任何输出时仍然失败（不静默吞错）。"""
    async def runner(command, cwd, timeout_seconds):
        return 3, "", "no output produced"

    adapter = _make_adapter(tmp_path, runner)
    try:
        await adapter.collect(
            CrawlRequest(topic="竹知了", platforms=["weibo"], time_range={})
        )
        assert False, "expected CrawlerExecutionError"
    except Exception as exc:  # noqa: BLE001
        assert "exit code 3" in str(exc)
