"""M8b: embedding model version registry, rebuild-aware backfill and the
version mismatch check."""

from __future__ import annotations

from datetime import UTC, datetime

from app.application.repositories import ApplicationRepository
from app.infrastructure.database import Database
from app.infrastructure.database.models import SourceCommentRecord, SourcePostRecord
from app.schemas.cases import CreateCaseRequest
from scripts.backfill_embeddings import _check_version, run_backfill


class _FakeEmbeddings:
    def __init__(self, version: str, dimensions: int = 4) -> None:
        self.model_version = version
        self._dimensions = dimensions
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [
            [float(index + 1) / 10] * self._dimensions for index in range(len(texts))
        ]

    async def health(self) -> dict[str, object]:
        return {"status": "healthy", "model_version": self.model_version}


async def _setup(tmp_path) -> tuple[Database, ApplicationRepository, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'versions.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="版本重建测试", platforms=["weibo"])
    )
    async with database.session_factory() as session:
        post = SourcePostRecord(
            case_id=case.id,
            platform="weibo",
            native_id="p1",
            content="测试内容",
            source_url="https://weibo.com/1",
            published_at=datetime(2026, 8, 1, tzinfo=UTC),
            content_hash="h1",
        )
        session.add(post)
        await session.flush()
        session.add(
            SourceCommentRecord(
                post_id=post.id,
                platform="weibo",
                native_id="c1",
                content="第一条评论内容足够长",
                published_at=datetime(2026, 8, 2, tzinfo=UTC),
            )
        )
        session.add(
            SourceCommentRecord(
                post_id=post.id,
                platform="weibo",
                native_id="c2",
                content="第二条评论内容足够长",
                published_at=datetime(2026, 8, 2, tzinfo=UTC),
            )
        )
        await session.commit()
    return database, repository, case.id


# ---------- version registry ----------


async def test_upsert_embedding_version_is_idempotent_per_version(tmp_path) -> None:
    database, repository, _ = await _setup(tmp_path)
    try:
        first = await repository.upsert_embedding_version(
            model_name="BAAI/bge-m3",
            model_version="v1",
            dimensions=1024,
            record_count=5,
        )
        second = await repository.upsert_embedding_version(
            model_name="BAAI/bge-m3",
            model_version="v1",
            dimensions=1024,
            record_count=12,
        )
        assert first.id == second.id
        assert second.record_count == 12
        assert second.rebuilt_at is not None
        registered = await repository.get_embedding_version("v1")
        assert registered is not None and registered.model_name == "BAAI/bge-m3"
    finally:
        await database.dispose()


async def test_list_embedding_versions_newest_first(tmp_path) -> None:
    database, repository, _ = await _setup(tmp_path)
    try:
        await repository.upsert_embedding_version(
            model_name="m1", model_version="old", dimensions=1024, record_count=1
        )
        await repository.upsert_embedding_version(
            model_name="m1", model_version="new", dimensions=1024, record_count=2
        )
        versions = await repository.list_embedding_versions(limit=10)
        assert versions[0].model_version == "new"
        assert versions[1].model_version == "old"
    finally:
        await database.dispose()


# ---------- rebuild-aware backfill ----------


async def test_backfill_default_only_fills_null_embeddings(tmp_path) -> None:
    database, repository, case_id = await _setup(tmp_path)
    try:
        fake = _FakeEmbeddings("v1")
        result = await run_backfill(
            database=database,
            embeddings=fake,
            source="comment",
            case_id=case_id,
        )
        assert result["updated"] == 2
        # second run: nothing is NULL anymore
        again = await run_backfill(
            database=database,
            embeddings=fake,
            source="comment",
            case_id=case_id,
        )
        assert again["updated"] == 0
    finally:
        await database.dispose()


async def test_backfill_rebuild_recomputes_every_row(tmp_path) -> None:
    database, repository, case_id = await _setup(tmp_path)
    try:
        fake = _FakeEmbeddings("v1")
        await run_backfill(database=database, embeddings=fake, source="comment")
        rebuild = await run_backfill(
            database=database,
            embeddings=fake,
            source="comment",
            rebuild=True,
        )
        assert rebuild["updated"] == 2  # rows were re-embedded, not skipped
        assert rebuild["considered"] == 2
    finally:
        await database.dispose()


# ---------- version mismatch check ----------


async def test_version_check_flags_unregistered_worker_version(tmp_path) -> None:
    database, repository, _ = await _setup(tmp_path)
    try:
        await repository.upsert_embedding_version(
            model_name="m", model_version="v1", dimensions=1024, record_count=1
        )
        assert not await _check_version(repository, _FakeEmbeddings("v1"))
        assert await _check_version(repository, _FakeEmbeddings("v2"))
        assert not await _check_version(repository, _FakeEmbeddings(None))
    finally:
        await database.dispose()
