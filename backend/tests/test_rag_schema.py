"""Schema checks: extended RAG sources carry embedding columns."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from app.infrastructure.database import Database


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row[1] for row in rows}
    finally:
        conn.close()


def test_extended_sources_have_embedding_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "rag_schema.db"
    database = Database(f"sqlite+aiosqlite:///{db_path}")

    async def create() -> None:
        await database.create_schema()

    asyncio.run(create())
    assert "embedding" in _columns(db_path, "artifacts")
    assert "embedding" in _columns(db_path, "source_comments")
    assert "embedding" in _columns(db_path, "claims")
    assert "embedding" in _columns(db_path, "evidence")

    asyncio.run(database.dispose())
