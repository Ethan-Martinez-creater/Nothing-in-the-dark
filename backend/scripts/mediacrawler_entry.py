"""COIFESP entrypoint for running MediaCrawler with an isolated browser."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    crawler_root = Path.cwd()
    sys.path.insert(0, str(crawler_root))

    import config  # type: ignore[import-not-found]

    # Keep account cookies out of the OS process command line. The adapter
    # supplies them only to this child process and we remove the environment
    # entry immediately after reading it.
    config.COOKIES = os.environ.pop("COIFESP_MEDIACRAWLER_COOKIES", "")

    # The upstream default waits for a manually started Chrome CDP endpoint.
    # COIFESP instead lets Playwright launch an isolated persistent context.
    config.ENABLE_CDP_MODE = False
    config.CDP_CONNECT_EXISTING = False

    from main import async_cleanup  # type: ignore[import-not-found]
    from main import main as crawler_main  # type: ignore[import-not-found]
    from tools.app_runner import run  # type: ignore[import-not-found]

    run(crawler_main, async_cleanup, cleanup_timeout_seconds=15.0)


if __name__ == "__main__":
    main()
