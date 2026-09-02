"""P0-1.3 / F-3.2: cancel a run kills the crawler subprocess."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.application.ports.crawler import CrawlRequest
from app.core.errors import ApplicationError
from app.harness.cancel import run_cancel_event
from app.harness.tools import ToolRegistry, ToolSpec
from app.infrastructure.crawler.mediacrawler import (
    MediaCrawlerAdapter,
    MediaCrawlerConfig,
    _TIMEOUT_EXIT_CODE,
    _run_command,
)


@pytest.mark.asyncio
async def test_run_command_kills_child_when_cancel_event_set() -> None:
    cancel = asyncio.Event()
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    task = asyncio.create_task(
        _run_command(command, Path.cwd(), 30, cancel_event=cancel)
    )
    await asyncio.sleep(0.3)
    cancel.set()
    with pytest.raises(ApplicationError) as exc:
        await task
    assert exc.value.code == "tool_cancelled"


@pytest.mark.asyncio
async def test_run_command_timeout_returns_exit_code_instead_of_raising() -> None:
    # 回归：超时强杀后应返回非零退出码（走 collect 的 partial 保留），
    # 而不是抛异常丢弃已采数据。
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    return_code, stdout, stderr = await _run_command(
        command, Path.cwd(), 0.2
    )
    assert return_code == _TIMEOUT_EXIT_CODE
    assert isinstance(stdout, str)
    assert isinstance(stderr, str)


@pytest.mark.asyncio
async def test_adapter_stops_before_next_platform_when_cancelled(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MediaCrawler"
    root.mkdir()
    (root / "main.py").write_text("# stub\n", encoding="utf-8")
    started: list[str] = []

    async def runner(command, cwd, timeout_seconds):
        started.append(command[command.index("--platform") + 1])
        await asyncio.sleep(0.05)
        return 0, "", ""

    adapter = MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=root,
            output_root=tmp_path / "out",
            python_executable=Path(sys.executable),
            entrypoint=root / "main.py",
        ),
        command_runner=runner,
    )
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(ApplicationError) as exc:
        await adapter.collect(
            CrawlRequest(
                topic="x",
                platforms=["weibo", "bilibili"],
                time_range={},
                cancel_event=cancel,
            )
        )
    assert exc.value.code == "tool_cancelled"
    assert started == []


@pytest.mark.asyncio
async def test_tool_handler_is_cancelled_when_outer_task_cancelled() -> None:
    started = asyncio.Event()
    finished = asyncio.Event()

    class Empty(BaseModel):
        pass

    async def slow(_arguments: BaseModel) -> dict:
        started.set()
        try:
            await asyncio.sleep(30)
            return {"ok": True}
        finally:
            finished.set()

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="slow",
            version="1.0.0",
            description="slow",
            input_model=Empty,
            handler=slow,
            timeout_seconds=60,
        )
    )
    cancel = asyncio.Event()
    token = run_cancel_event.set(cancel)
    try:
        task = asyncio.create_task(
            registry.invoke_with_meta("slow", {}, cancel_event=cancel)
        )
        await started.wait()
        cancel.set()
        with pytest.raises(ApplicationError) as exc:
            await task
        assert exc.value.code == "tool_cancelled"
        await asyncio.wait_for(finished.wait(), timeout=2)
    finally:
        run_cancel_event.reset(token)
