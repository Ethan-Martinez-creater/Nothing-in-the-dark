"""Backfill embeddings for RAG sources created before the embedding columns.

Usage:
    python -m scripts.backfill_embeddings [--source comment|artifact|claim|evidence|all]
                                          [--case-id <case_id>]
                                          [--batch-size 16]
                                          [--rebuild]

Idempotent: by default only rows whose embedding column is NULL are
processed, so the script can be re-run after partial failures or a worker
outage. ``--rebuild`` recomputes every row (used after a model swap) and
re-registers the worker's model version in ``embedding_versions``. When the
worker reports a different version than the registered one, the script
warns and suggests ``--rebuild`` instead of silently mixing vectors.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from app.application.repositories import ApplicationRepository
from app.core.config import get_settings
from app.infrastructure.database import Database
from app.infrastructure.database.models import (
    ArtifactRecord,
    ClaimRecord,
    EvidenceRecord,
    SourceCommentRecord,
)
from app.infrastructure.embeddings import EmbeddingWorkerClient

_TEXT_OF: dict[str, tuple[Any, Callable[[Any], str]]] = {
    "comment": (SourceCommentRecord, lambda r: r.content),
    "artifact": (ArtifactRecord, lambda r: f"{r.title} {r.data}"),
    "claim": (ClaimRecord, lambda r: r.text),
    "evidence": (EvidenceRecord, lambda r: r.excerpt),
}


async def run_backfill(
    *,
    database: Database,
    embeddings: Any,
    source: str = "all",
    case_id: str | None = None,
    batch_size: int = 16,
    rebuild: bool = False,
) -> dict[str, int]:
    """Embed rows (NULL-only, or every row with ``rebuild``)."""
    if source != "all" and source not in _TEXT_OF:
        raise ValueError(f"unknown source '{source}'")
    sources = list(_TEXT_OF) if source == "all" else [source]
    updated = 0
    total = 0
    for key in sources:
        model, text_of = _TEXT_OF[key]
        async with database.session_factory() as session:
            query = select(model)
            if not rebuild:
                query = query.where(model.embedding.is_(None))
            if case_id and hasattr(model, "case_id"):
                query = query.where(model.case_id == case_id)
            records = (await session.scalars(query)).all()
        total += len(records)
        for start in range(0, len(records), batch_size):
            batch = records[start : start + batch_size]
            vectors = await embeddings.embed([text_of(record) for record in batch])
            if vectors is None:
                break  # worker unavailable: keep rows untouched
            async with database.session_factory() as session:
                for record, vector in zip(batch, vectors, strict=True):
                    current = await session.get(model, record.id)
                    if current is not None:
                        current.embedding = vector
                await session.commit()
            updated += len(batch)
    return {"updated": updated, "considered": total}


async def _check_version(
    repository: ApplicationRepository,
    embeddings: EmbeddingWorkerClient,
) -> bool:
    """True when the worker model version differs from the registered one.

    The caller prints the warning; kept side-effect free so tests can
    assert the version decision directly.
    """
    version = embeddings.model_version
    if version is None:
        return False
    registered = await repository.get_embedding_version(version)
    if registered is not None:
        return False
    latest = await repository.list_embedding_versions(limit=1)
    return bool(latest)


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Backfill RAG embeddings")
    parser.add_argument(
        "--source",
        choices=[*_TEXT_OF, "all"],
        default="all",
        help="Which source table to backfill (default: all).",
    )
    parser.add_argument("--case-id", default=None, help="Restrict to one case.")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Recompute every row (use after a model swap).",
    )
    args = parser.parse_args()

    settings = get_settings()
    database = Database(settings.database_url)
    await database.create_schema()
    repository = ApplicationRepository(database)
    embeddings = EmbeddingWorkerClient(
        settings.embedding_worker_url,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    if not args.rebuild:
        try:
            await embeddings.health()
        except Exception as exc:  # noqa: BLE001 - script-level reporting
            print(f"SKIP: embedding worker unreachable ({exc}); nothing to do.")
            await database.dispose()
            return
        if await _check_version(repository, embeddings):
            latest = await repository.list_embedding_versions(limit=1)
            print(
                f"WARNING: worker model version '{embeddings.model_version}' "
                f"differs from the registered '{latest[0].model_version}'; "
                "vectors may mix two models. Re-run with --rebuild."
            )
    result = await run_backfill(
        database=database,
        embeddings=embeddings,
        source=args.source,
        case_id=args.case_id,
        batch_size=args.batch_size,
        rebuild=args.rebuild,
    )
    if embeddings.model_version:
        await repository.upsert_embedding_version(
            model_name=settings.embedding_model,
            model_version=embeddings.model_version,
            dimensions=settings.embedding_dimensions,
            record_count=result["updated"],
        )
    print(result)
    await database.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
