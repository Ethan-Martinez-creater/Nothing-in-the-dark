"""backfill_embeddings: idempotent embedding backfill for RAG sources."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.infrastructure.database import Database
from app.infrastructure.database.models import (
    ArtifactRecord,
    ClaimRecord,
    EvidenceRecord,
    SourceCommentRecord,
    SourcePostRecord,
)
from scripts.backfill_embeddings import run_backfill


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[0.1] * 1024 for _ in texts]


async def _seed(database: Database) -> str:
    from app.application.repositories import ApplicationRepository
    from app.schemas.cases import CreateCaseRequest

    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="新能源汽车争议", platforms=["weibo"])
    )
    async with database.session_factory() as session:
        post = SourcePostRecord(
            case_id=case.id,
            platform="weibo",
            native_id="p1",
            content="召回公告",
            content_hash="h1",
        )
        session.add(post)
        await session.flush()
        session.add(
            SourceCommentRecord(
                post_id=post.id,
                platform="weibo",
                native_id="c1",
                content="评论内容",
            )
        )
        session.add(
            ArtifactRecord(
                case_id=case.id,
                kind="fact_check",
                title="核查卡",
                version=1,
                data={"verdict": "supported"},
            )
        )
        session.add(
            ClaimRecord(
                case_id=case.id,
                text="主张内容",
                status="open",
                created_by_run_id="00000000-0000-0000-0000-000000000001",
            )
        )
        session.add(
            EvidenceRecord(
                case_id=case.id,
                source_type="social_post",
                source_id="p1",
                stance="support",
                excerpt="证据摘录",
            )
        )
        await session.commit()
    return case.id


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'backfill.db'}")
    asyncio.run(database.create_schema())

    async def run() -> None:
        case_id = await _seed(database)
        embeddings = FakeEmbeddingClient()
        first = await run_backfill(
            database=database,
            embeddings=embeddings,
            source="all",
            case_id=case_id,
            batch_size=2,
        )
        assert first["updated"] == 4  # comment + artifact + claim + evidence
        second = await run_backfill(
            database=database,
            embeddings=embeddings,
            source="all",
            case_id=case_id,
            batch_size=2,
        )
        assert second["updated"] == 0  # nothing left to embed

    asyncio.run(run())
    asyncio.run(database.dispose())


def test_backfill_keeps_failed_rows_null(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'backfill.db'}")
    asyncio.run(database.create_schema())

    async def run() -> None:
        case_id = await _seed(database)

        class UnavailableClient(FakeEmbeddingClient):
            async def embed(self, texts: list[str]) -> list[list[float]] | None:
                return None  # worker unavailable

        result = await run_backfill(
            database=database,
            embeddings=UnavailableClient(),
            source="claim",
            case_id=case_id,
        )
        assert result["updated"] == 0  # rows stay NULL, re-runnable

    asyncio.run(run())
    asyncio.run(database.dispose())
