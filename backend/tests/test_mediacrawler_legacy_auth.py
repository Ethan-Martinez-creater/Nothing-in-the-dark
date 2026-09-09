"""Phase 1 regression: legacy Chromium profile markers must not imply login.

Windows 迁移的 browser_data 到 Linux 后，Cookies SQLite 中可能仍存在
登录标志 cookie 名称（SUB / SESSDATA / ...），但值无法在 Linux 解密或
已失效。旧的 `_platform_login_state()` 仅凭名称存在就判定"已登录"，
导致 headless 假阳性。本轮将其降级为诊断辅助 `_legacy_profile_has_login_markers`，
认证决策只认应用凭据（注入的 cookie 字符串）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.infrastructure.crawler.mediacrawler import (
    MediaCrawlerAdapter,
    MediaCrawlerConfig,
)


def _write_fake_profile_cookies(
    root: Path,
    platform_code: str,
    cookie_names: list[str],
) -> Path:
    """在临时 MediaCrawler 根下构造带标志 cookie 名称的 Chromium Cookies 库。"""
    cookies_db = (
        root
        / "browser_data"
        / f"{platform_code}_user_data_dir"
        / "Default"
        / "Network"
        / "Cookies"
    )
    cookies_db.parent.mkdir(parents=True)
    conn = sqlite3.connect(cookies_db)
    try:
        conn.execute(
            "CREATE TABLE cookies (name TEXT, value TEXT, domain TEXT)"
        )
        for name in cookie_names:
            conn.execute(
                "INSERT INTO cookies (name, value, domain) VALUES (?, ?, ?)",
                (name, "encrypted-payload-that-linux-cannot-decode", ".example.com"),
            )
        conn.commit()
    finally:
        conn.close()
    return cookies_db


def _make_adapter(root: Path, **overrides: object) -> MediaCrawlerAdapter:
    defaults: dict[str, object] = {
        "root": root,
        "output_root": root / "crawls",
        "login_type": "qrcode",
        "headless": True,
    }
    defaults.update(overrides)
    return MediaCrawlerAdapter(MediaCrawlerConfig(**defaults))


def test_profile_marker_without_app_credential_is_not_authenticated(
    tmp_path: Path,
) -> None:
    """Cookies 库存在 SESSDATA 名称但无应用 cookie 配置 => 不得判定已登录。"""
    root = tmp_path / "MediaCrawler"
    root.mkdir()
    _write_fake_profile_cookies(root, "bili", ["SESSDATA", "bili_jct"])

    adapter = _make_adapter(root)

    # 诊断函数能识别残留标志（保留排查能力）……
    assert adapter._legacy_profile_has_login_markers("bilibili") is True
    # ……但 headless 决策不再据此判定"已登录可后台采集"。
    assert adapter._effective_headless("bilibili") is False


def test_profile_marker_with_cookie_config_stays_headless(
    tmp_path: Path,
) -> None:
    """显式配置 cookie 字符串时仍保持后台 headless（正常路径不破坏）。"""
    root = tmp_path / "MediaCrawler"
    root.mkdir()
    _write_fake_profile_cookies(root, "wb", ["SUB"])

    adapter = _make_adapter(
        root,
        login_type="cookie",
        weibo_cookies="SUB=opaque-value; WBPSESS=opaque",
    )
    assert adapter._effective_headless("weibo") is True


def test_unknown_platform_has_no_markers(tmp_path: Path) -> None:
    """无 marker 定义或 profile 缺失时返回 False，不再保守返回 True。"""
    root = tmp_path / "MediaCrawler"
    root.mkdir()
    adapter = _make_adapter(root)
    assert adapter._legacy_profile_has_login_markers("zhihu") is False


def test_headless_false_respected_without_credential(tmp_path: Path) -> None:
    """配置 headless=false 时保持前台，即使 profile 有标志。"""
    root = tmp_path / "MediaCrawler"
    root.mkdir()
    _write_fake_profile_cookies(root, "wb", ["SUB"])
    adapter = _make_adapter(root, headless=False)
    assert adapter._effective_headless("weibo") is False
