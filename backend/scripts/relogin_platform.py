"""Relogin a single MediaCrawler platform and persist its browser login state.

Runs the vendored MediaCrawler login flow (qrcode) inside an isolated
persistent browser context rooted at ``browser_data/<platform>_user_data_dir``
so that subsequent system crawls reuse the saved session. One platform per
invocation; call this script repeatedly (weibo -> bilibili -> tieba -> zhihu
-> douyin) to avoid popping all platform QR-code windows at once.

Usage::

    E:/miniconda3/envs/bettafish/python.exe \\
        backend/scripts/relogin_platform.py <platform>

Exit codes: 0 = login state confirmed, 2 = timeout/user aborted.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

PLATFORM_CODES = {
    "weibo": "wb",
    "bilibili": "bili",
    "tieba": "tieba",
    "zhihu": "zhihu",
    "douyin": "dy",
}

INDEX_URLS = {
    "weibo": "https://www.weibo.com",
    "bilibili": "https://www.bilibili.com",
    "tieba": "https://tieba.baidu.com",
    "zhihu": "https://www.zhihu.com",
    "douyin": "https://www.douyin.com",
}

# Cookies that prove the platform account is logged in.
LOGIN_COOKIE_KEYS = {
    "weibo": ("SSOLoginState", "WBPSESS"),
    "bilibili": ("SESSDATA", "DedeUserID"),
    "tieba": ("BDUSS", "BDUSS_BFESS", "STOKEN", "PTOKEN"),
    "zhihu": ("z_c0",),
    "douyin": ("LOGIN_STATUS",),
}


def _media_crawler_root() -> Path:
    # backend/scripts/relogin_platform.py -> project root / vendor / MediaCrawler
    return Path(__file__).resolve().parents[2] / "vendor" / "MediaCrawler"


async def _has_login_cookie(context: object, keys: tuple[str, ...]) -> bool:
    from tools import utils  # noqa: PLC0415  (imported after chdir)

    cookies = await context.cookies()
    _, cookie_dict = utils.convert_cookies(cookies)
    return any(cookie_dict.get(name) for name in keys)


async def _poll_login(
    context: object,
    keys: tuple[str, ...],
    timeout_seconds: float,
) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        if await _has_login_cookie(context, keys):
            return True
        await asyncio.sleep(2)
    return False


async def run(platform: str) -> int:
    root = _media_crawler_root()
    code = PLATFORM_CODES[platform]
    os.chdir(root)
    sys.path.insert(0, str(root))

    import config  # noqa: PLC0415

    config.PLATFORM = code
    config.LOGIN_TYPE = "qrcode"
    config.COOKIES = ""
    config.HEADLESS = False
    config.SAVE_LOGIN_STATE = True
    config.ENABLE_CDP_MODE = False
    config.CDP_CONNECT_EXISTING = False

    user_data_dir = root / "browser_data" / f"{code}_user_data_dir"
    user_data_dir.mkdir(parents=True, exist_ok=True)

    print(f"[relogin] platform={platform} code={code} user_data_dir={user_data_dir}")

    from playwright.async_api import async_playwright  # noqa: PLC0415

    async with async_playwright() as p:
        browser_context = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            channel="chrome",
            headless=False,
            accept_downloads=True,
            viewport={"width": 1920, "height": 1080},
        )
        page = (
            browser_context.pages[0]
            if browser_context.pages
            else await browser_context.new_page()
        )

        # 1) If a valid session already exists, do not pop the QR window again.
        keys = LOGIN_COOKIE_KEYS[platform]
        if await _has_login_cookie(browser_context, keys):
            print(f"[relogin] {platform} already logged in (session cookie present); skip")
            await browser_context.close()
            return 0

        # 2) Open the platform home page so the login dialog can render.
        await page.goto(INDEX_URLS[platform], wait_until="domcontentloaded", timeout=60_000)

        # 3) Try the platform's own QR-code login flow; fall back to manual.
        try:
            login_class = {
                "weibo": ("media_platform.weibo.login", "WeiboLogin"),
                "bilibili": ("media_platform.bilibili.login", "BilibiliLogin"),
                "tieba": ("media_platform.tieba.login", "BaiduTieBaLogin"),
                "zhihu": ("media_platform.zhihu.login", "ZhiHuLogin"),
                "douyin": ("media_platform.douyin.login", "DouYinLogin"),
            }[platform]
            module = __import__(login_class[0], fromlist=[login_class[1]])
            login_obj = getattr(module, login_class[1])(
                login_type="qrcode",
                login_phone="",
                browser_context=browser_context,
                context_page=page,
                cookie_str="",
            )
            print(f"[relogin] {platform}: QR login window opened. Scan now…")
            await login_obj.begin()
        except SystemExit:
            # The platform flow bailed (e.g. QR element not found). Fall through
            # to manual completion inside the same visible browser window.
            print(f"[relogin] {platform}: automatic QR flow unavailable; complete login manually in the opened window.")
        except Exception as exc:  # noqa: BLE001
            print(f"[relogin] {platform}: login flow raised {type(exc).__name__}: {exc}")
            print(f"[relogin] {platform}: complete login manually in the opened window.")

        # 4) Wait until the session cookie appears or the timeout elapses.
        print(f"[relogin] {platform}: waiting up to 240s for login state…")
        ok = await _poll_login(browser_context, keys, timeout_seconds=240.0)
        if ok:
            print(f"[relogin] {platform}: LOGIN_OK")
            await asyncio.sleep(3)
            await browser_context.close()
            return 0
        print(f"[relogin] {platform}: LOGIN_TIMEOUT")
        await browser_context.close()
        return 2


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in PLATFORM_CODES:
        print(
            f"usage: relogin_platform.py <{'|'.join(PLATFORM_CODES)}>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(asyncio.run(run(sys.argv[1])))


if __name__ == "__main__":
    main()
