"""Restore the deep zhihu collection data that was orphaned by the outer-sandbox
timeout race.

The run a282d44d timed out: the outer sandbox (1800s) killed the process tree
before the inner MediaCrawler timeout could return exit code 124 and preserve
partial data, so the JSONL written to
``data/crawls/<run>-zhihu-1/`` was never ingested. This script re-reads that
output and persists it through the same path the worker would have used
(``CollectionRunWorker._ingest_platform``), which applies the collection
exclusions, coverage sampling and upserts by (case, platform, native_id) —
already-persisted posts are updated, not duplicated.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.collection_run_worker import CollectionRunWorker  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.infrastructure.crawler.mediacrawler import MediaCrawlerAdapter  # noqa: E402
from app.infrastructure.database import Database  # noqa: E402
from app.infrastructure.database.collection_run_repository import CollectionRunRepository  # noqa: E402
from app.infrastructure.database.social_repository import SocialRepository  # noqa: E402

RUN_ID = "a282d44d-ecc9-47ec-a84f-ebb924dd6533"
PLATFORM = "zhihu"
OUTPUT_DIR = Path("data/crawls") / f"{RUN_ID}-{PLATFORM}-1" / PLATFORM


async def main() -> int:
    settings = get_settings()
    db = Database(settings.database_url)
    repository = CollectionRunRepository(db)
    social = SocialRepository(db)

    # Load the immutable snapshot the run was executed with.
    run = await repository.get(RUN_ID)
    snapshot = dict(run.request_json or {})
    print("snapshot phase:", snapshot.get("phase"), "| platforms:", snapshot.get("platforms"))
    print("snapshot budget:", snapshot.get("budget"))

    # Re-read the JSONL output exactly as the worker would.
    adapter = MediaCrawlerAdapter.__new__(MediaCrawlerAdapter)
    posts = adapter._load_platform_posts(PLATFORM, OUTPUT_DIR)
    print("recovered posts from JSONL:", len(posts))

    worker = CollectionRunWorker(repository, None, social)  # type: ignore[arg-type]
    kept, comments = await worker._ingest_platform(
        RUN_ID,
        PLATFORM,
        attempt=1,
        posts=posts,
        snapshot=snapshot,
    )
    print("persisted posts:", kept)
    print("persisted comments:", comments)

    # Verify what landed in the DB (count by platform).
    from sqlalchemy import func, select

    from app.infrastructure.database.models import SourcePostRecord

    async with db.session_factory() as session:
        total = await session.scalar(
            select(func.count()).select_from(SourcePostRecord).where(
                SourcePostRecord.case_id == str(snapshot.get("case_id") or ""),
                SourcePostRecord.platform == PLATFORM,
            )
        )
    print("total zhihu posts now in DB:", total)
    await db.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
