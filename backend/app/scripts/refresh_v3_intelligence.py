"""V3 §66: Historical backfill — 为历史 Case enqueue alignment + integrity。

用法（仓库根目录 backend/）：

```bash
python -m app.scripts.refresh_v3_intelligence --all
python -m app.scripts.refresh_v3_intelligence --case-id <case_id>
```

脚本只 enqueue（不等待、不跑算法）；幂等 key 固定：
backfill-v3:{job_type}:{case_id}:{V3_INTELLIGENCE_VERSION}。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from app.core.config import Settings
from app.core.v3 import V3_INTELLIGENCE_VERSION

logger = logging.getLogger("refresh_v3_intelligence")


def _build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V3 Intelligence historical backfill")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="enqueue for every case")
    group.add_argument("--case-id", metavar="CASE_ID", help="enqueue for one case")
    return parser.parse_args()


async def _enqueue_for_case(
    container: Any, case_id: str
) -> dict[str, str]:
    job_ids: dict[str, str] = {}
    for job_type in ("alignment", "integrity"):
        job = await container.analysis_job_repository.create_job(
            case_id=case_id,
            job_type=job_type,
            idempotency_key=(
                f"backfill-v3:{job_type}:{case_id}:{V3_INTELLIGENCE_VERSION}"
            ),
        )
        job_ids[job_type] = job.id
    return job_ids


async def _run(args: argparse.Namespace) -> int:
    from app.bootstrap import ApplicationContainer

    container = ApplicationContainer(Settings())
    await container.start()
    try:
        if args.case_id:
            cases: list[Any] = [await container.repository.get_case(args.case_id)]
        else:
            cases = list(
                await container.repository.list_cases_ordered_by_creation()
            )
        if not cases:
            logger.info("no cases to backfill")
            return 0
        enqueued = 0
        for case in cases:
            job_ids = await _enqueue_for_case(container, case.id)
            enqueued += 1
            logger.info(
                "case %s → alignment=%s integrity=%s",
                case.id,
                job_ids["alignment"],
                job_ids["integrity"],
            )
        logger.info("backfill done: %d cases enqueued", enqueued)
        return 0
    finally:
        await container.stop()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _build_args()
    try:
        return asyncio.run(_run(args))
    except Exception as exc:  # pragma: no cover - CLI 边界
        logger.error("backfill failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
