"""Phase 3 tests: MediaCrawler COIFESP auth bridge.

覆盖 vendor/MediaCrawler/tools/coifesp_auth_bridge.py 与
show_qrcode 的 COIFESP 分支（headless 服务器导出 qr.json，
不弹 GUI；登录成功后导出 auth_state.json；无 env 时保持原行为）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

VENDOR_ROOT = Path(__file__).resolve().parents[2] / "vendor" / "MediaCrawler"


class _QuietLogger:
    def info(self, *args, **kwargs):  # noqa: ANN002, ANN003
        pass


def _stub_heavy_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """加载真实 crawler_util 但断开 MediaCrawler 重依赖（PIL/cv2 等）。

    tools 包用带 __path__ 的 stub 容器定位真实 crawler_util.py；相对导入
    的 utils/httpx_util 用 stub 顶替，模块级 PIL/playwright 导入注入假模块。
    """
    tools_pkg = ModuleType("tools")
    tools_pkg.__path__ = [str(VENDOR_ROOT / "tools")]
    utils_stub = ModuleType("tools.utils")
    utils_stub.logger = _QuietLogger()
    httpx_util_stub = ModuleType("tools.httpx_util")
    httpx_util_stub.make_async_client = lambda *a, **kw: None  # noqa: ARG005
    monkeypatch.setitem(sys.modules, "tools", tools_pkg)
    monkeypatch.setitem(sys.modules, "tools.utils", utils_stub)
    monkeypatch.setitem(sys.modules, "tools.httpx_util", httpx_util_stub)

    pil = ModuleType("PIL")
    for name in ("Image", "ImageDraw", "ImageShow"):
        sub = ModuleType(f"PIL.{name}")
        setattr(pil, name, sub)
        monkeypatch.setitem(sys.modules, f"PIL.{name}", sub)
    monkeypatch.setitem(sys.modules, "PIL", pil)

    playwright = ModuleType("playwright")
    async_api = ModuleType("playwright.async_api")
    for name in ("BrowserContext", "Cookie", "Page"):
        setattr(async_api, name, type(name, (), {}))
    playwright.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)


def _load_bridge(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """加载 bridge 模块（tools 包以 stub 注入，避免 MediaCrawler 全量依赖）。"""
    tools_pkg = ModuleType("tools")
    utils_stub = ModuleType("tools.utils")
    utils_stub.logger = _QuietLogger()
    tools_pkg.utils = utils_stub
    monkeypatch.setitem(sys.modules, "tools", tools_pkg)
    monkeypatch.setitem(sys.modules, "tools.utils", utils_stub)
    spec = importlib.util.spec_from_file_location(
        "coifesp_auth_bridge", VENDOR_ROOT / "tools" / "coifesp_auth_bridge.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_publish_qrcode_writes_parseable_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = _load_bridge(monkeypatch)
    monkeypatch.setenv("COIFESP_AUTH_SESSION_DIR", str(tmp_path))
    bridge.publish_qrcode("raw-base64-data")
    payload = json.loads((tmp_path / "qr.json").read_text(encoding="utf-8"))
    assert payload["type"] == "qrcode"
    assert payload["data_url"] == "data:image/png;base64,raw-base64-data"
    # 已带 data: 前缀时不重复加
    bridge.publish_qrcode("data:image/png;base64,already-prefixed")
    payload2 = json.loads((tmp_path / "qr.json").read_text(encoding="utf-8"))
    assert payload2["data_url"] == "data:image/png;base64,already-prefixed"


def test_export_cookies_writes_auth_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = _load_bridge(monkeypatch)
    monkeypatch.setenv("COIFESP_AUTH_SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("COIFESP_AUTH_PLATFORM", "weibo")

    class FakeContext:
        async def cookies(self) -> list[dict[str, object]]:
            return [
                {
                    "name": "SUB",
                    "value": "secret-cookie-value",
                    "domain": ".weibo.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                }
            ]

    asyncio.run(bridge.export_cookies(FakeContext(), platform="weibo"))
    state = json.loads((tmp_path / "auth_state.json").read_text(encoding="utf-8"))
    assert state["version"] == 1
    assert state["platform"] == "weibo"
    assert state["cookies"][0]["name"] == "SUB"
    assert state["cookies"][0]["value"] == "secret-cookie-value"
    # Linux 下文件权限尽量 0600；Windows chmod 不可用时容错。
    if os.name != "nt":
        assert (tmp_path / "auth_state.json").stat().st_mode & 0o777 == 0o600


def test_export_cookies_filters_invalid_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """name/value/domain 无效的 cookie（如百度的 nameless cookie）不导出。"""
    bridge = _load_bridge(monkeypatch)
    monkeypatch.setenv("COIFESP_AUTH_SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("COIFESP_AUTH_PLATFORM", "tieba")

    class FakeContext:
        async def cookies(self) -> list[dict[str, object]]:
            return [
                {"name": "", "value": "v", "domain": ".baidu.com", "path": "/"},
                {"name": "BDUSS", "value": "", "domain": ".baidu.com", "path": "/"},
                {"name": "STOKEN", "value": "ok"},  # 缺 domain
                {
                    "name": "PTOKEN",
                    "value": "good",
                    "domain": ".baidu.com",
                    "path": "/",
                },
            ]

    asyncio.run(bridge.export_cookies(FakeContext(), platform="tieba"))
    state = json.loads((tmp_path / "auth_state.json").read_text(encoding="utf-8"))
    assert [cookie["name"] for cookie in state["cookies"]] == ["PTOKEN"]


def test_no_session_env_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = _load_bridge(monkeypatch)
    monkeypatch.delenv("COIFESP_AUTH_SESSION_DIR", raising=False)
    bridge.publish_qrcode("abc")
    assert not (tmp_path / "qr.json").exists()
    assert bridge.is_coifesp_auth_session() is False


def test_show_qrcode_uses_bridge_without_gui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有 COIFESP_AUTH_SESSION_DIR 时 show_qrcode 走 bridge 且不弹窗。"""
    # stub 容器 + 真实 crawler_util.py（断开 PIL/cv2 等重依赖）。
    _stub_heavy_imports(monkeypatch)
    import tools.crawler_util as crawler_util  # type: ignore[import-not-found]

    published: list[str] = []
    monkeypatch.setattr(
        "tools.coifesp_auth_bridge.is_coifesp_auth_session", lambda: True
    )
    monkeypatch.setattr(
        "tools.coifesp_auth_bridge.publish_qrcode",
        lambda code: published.append(code),
    )
    crawler_util.show_qrcode("data:image/png;base64,qr-payload")
    assert published == ["data:image/png;base64,qr-payload"]
