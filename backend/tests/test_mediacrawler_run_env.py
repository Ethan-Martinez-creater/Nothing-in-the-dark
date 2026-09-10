"""_run_command 的子进程环境构建回归测试。

systemd 起的 backend 环境没有 XDG_RUNTIME_DIR；白名单缺它导致 Chrome
创建 browser context 时立即崩溃（正式采集五平台全败的根因）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.infrastructure.crawler.mediacrawler import _COOKIE_ENV, _run_command


@pytest.mark.asyncio
async def test_run_command_injects_xdg_runtime_dir(tmp_path: Path) -> None:
    """os.environ 无 XDG_RUNTIME_DIR 时应 setdefault /run/user/<uid>。"""
    monkeypatch_env = {"PATH": "/usr/bin:/bin"}
    code, out, _err = await _run_command(
        [sys.executable, "-c", "import os; print(os.environ.get('XDG_RUNTIME_DIR', ''))"],
        cwd=tmp_path,
        timeout_seconds=30,
        process_environment=monkeypatch_env,
    )
    assert code == 0
    # /run/user/<uid> 仅 POSIX 存在；Windows 下仍不应报错（值为空即可）。
    if sys.platform != "win32":
        assert out.strip().startswith("/run/user/")


@pytest.mark.asyncio
async def test_run_command_keeps_existing_xdg_runtime_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """os.environ 已有 XDG_RUNTIME_DIR 时不得覆盖。"""
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/custom")
    code, out, _err = await _run_command(
        [sys.executable, "-c", "import os; print(os.environ.get('XDG_RUNTIME_DIR', ''))"],
        cwd=tmp_path,
        timeout_seconds=30,
    )
    assert code == 0
    if sys.platform != "win32":
        assert out.strip() == "/run/user/custom"


@pytest.mark.asyncio
async def test_run_command_passes_cookie_env(tmp_path: Path) -> None:
    """cookie 经 _COOKIE_ENV 传入子进程环境（不落命令行）。"""
    code, out, _err = await _run_command(
        [
            sys.executable,
            "-c",
            f"import os; print(len(os.environ.get({_COOKIE_ENV!r}, '')) > 0)",
        ],
        cwd=tmp_path,
        timeout_seconds=30,
        process_environment={_COOKIE_ENV: "SUB=opaque; SUB2=opaque2"},
    )
    assert code == 0
    assert out.strip().endswith("True")
