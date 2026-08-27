"""Backfill claims/evidence from existing fact_check artifacts.

核查专家在 2026-08-10 之前不走 verify_claims 工具，核查卡只进 artifact，
claims/evidence 表为空（证据侧栏显示 0）。本脚本对已有案例的 fact_check
artifact 补写 claims/evidence（幂等：已存在同文本主张的案例跳过）。

用法：.venv/Scripts/python.exe scripts/backfill_fact_check_claims.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.application.graph_worker import _persist_fact_check_cards
from app.application.repositories import ApplicationRepository
from app.infrastructure.database import Database
from app.infrastructure.database.social_repository import SocialRepository

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill")


async def main() -> None:
    import os

    database = Database(os.environ.get("DATABASE_URL", "postgresql+asyncpg://coifesp:123456@127.0.0.1:5432/coifesp_agent"))
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)

    async with database.session_factory() as session:
        from sqlalchemy import select

        from app.infrastructure.database.models import ArtifactRecord

        artifacts = (
            await session.scalars(
                select(ArtifactRecord).where(ArtifactRecord.kind == "fact_check")
            )
        ).all()

    total_claims = 0
    for artifact in artifacts:
        data = artifact.data
        if not isinstance(data, dict) or not data.get("cards"):
            continue
        # 幂等：该 case 已有 claims 则跳过
        existing = await repository.list_claims_by_case(artifact.case_id)
        if existing:
            continue
        try:
            await _persist_fact_check_cards(
                data,
                repository=repository,
                social=social,
                case_id=artifact.case_id,
                run_id=artifact.run_id,
            )
        except Exception:
            logger.exception("backfill failed for artifact %s", artifact.id)
            continue
        claims = await repository.list_claims_by_case(artifact.case_id)
        total_claims += len(claims)
        logger.info(
            "case %s: %d claims persisted from artifact %s",
            artifact.case_id[:8], len(claims), artifact.id[:8],
        )
    logger.info("backfill done, %d claims total", total_claims)
    await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
