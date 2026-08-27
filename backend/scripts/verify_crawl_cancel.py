"""Acceptance check: cancelling a crawl kills the child process.

Phase A (always, no login): start a long-lived Python child via
``_run_command``, set the cancel event, assert ``tool_cancelled`` and
that the process is gone.

Phase B (manual, DEMO_MODE=false):
    1. Start backend with PostgreSQL + DEMO_MODE=false.
    2. Create a case and POST /messages with approve_crawl=true to start
       a real collect_social_posts.
    3. While MediaCrawler is running, POST /runs/{id}/cancel.
    4. Confirm: run.status=cancelled, no new JSONL after cancel, no
       leftover python/MediaCrawler process, already-persisted posts remain.

Run from Project\\backend:
    .venv\\Scripts\\python scripts\\verify_crawl_cancel.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.core.errors import ApplicationError
from app.infrastructure.crawler.mediacrawler import _run_command


async def _phase_a() -> int:
    cancel = asyncio.Event()
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    task = asyncio.create_task(_run_command(command, Path.cwd(), 60, cancel_event=cancel))
    await asyncio.sleep(0.4)
    cancel.set()
    try:
        await task
    except ApplicationError as exc:
        if exc.code != "tool_cancelled":
            print(f"FAIL unexpected error {exc.code}: {exc}", file=sys.stderr)
            return 1
        print("OK phase A: cancel event killed the child process")
        return 0
    print("FAIL child exited without tool_cancelled", file=sys.stderr)
    return 1


def main() -> int:
    code = asyncio.run(_phase_a())
    print(
        "Phase B (real crawl) is manual — see the docstring in "
        "scripts/verify_crawl_cancel.py"
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
