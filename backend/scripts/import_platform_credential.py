"""One-off importer: load an auth_state.json export into encrypted storage.

Usage:
    python scripts/import_platform_credential.py <platform> <path/to/auth_state.json>

The file is the coifesp_auth_bridge export produced by a successful login
(see vendor/MediaCrawler/tools/coifesp_auth_bridge.py). Cookies are validated
and encrypted via PlatformAuthService; the plaintext file is deleted on
success. Cookie values are never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap import ApplicationContainer  # noqa: E402
from app.core.config import Settings  # noqa: E402


def _cookie_valid(cookie: object) -> bool:
    return (
        isinstance(cookie, dict)
        and all(
            isinstance(cookie.get(field), str) and cookie.get(field)
            for field in ("name", "value", "domain")
        )
    )


async def _import(platform: str, source: Path) -> int:
    settings = Settings()
    context = ApplicationContainer(settings)
    service = context.platform_auth_service
    if not service.enabled:
        print("ERROR: platform auth is not configured (master key missing)")
        return 2

    payload = json.loads(source.read_text(encoding="utf-8"))  # noqa: ASYNC240 - one-off admin tool
    cookies = payload.get("cookies")
    if not isinstance(cookies, list):
        print("ERROR: auth_state.json has no cookies list")
        return 2
    cookies = [cookie for cookie in cookies if _cookie_valid(cookie)]
    if not cookies:
        print("ERROR: no valid cookies in auth_state.json")
        return 2

    await service.save_credential(
        platform,
        {
            "format_version": 1,
            "platform": platform,
            "cookies": cookies,
        },
    )
    record = await service.get_record(platform)
    status = getattr(record, "status", "unknown")
    source.unlink(missing_ok=True)  # noqa: ASYNC240 - one-off admin tool
    print(f"imported: platform={platform} cookies={len(cookies)} status={status}")
    print("plaintext source file deleted")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=("weibo", "bilibili", "tieba", "zhihu", "douyin"))
    parser.add_argument("source", type=Path, help="path to auth_state.json")
    args = parser.parse_args()

    import asyncio

    return asyncio.run(_import(args.platform, args.source))


if __name__ == "__main__":
    raise SystemExit(main())
