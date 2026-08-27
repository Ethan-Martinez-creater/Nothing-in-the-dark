"""Add PostgreSQL full-text and pgvector indexes for hybrid retrieval.

Revision ID: 20260730_0004
Revises: 20260730_0003
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260730_0004"
down_revision: str | None = "20260730_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEARCH_TABLES = ("memories", "knowledge_chunks", "source_posts")


def upgrade() -> None:
    for table_name in SEARCH_TABLES:
        op.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('simple', coalesce(content, ''))
            ) STORED
            """
        )
        op.execute(
            f"""
            CREATE INDEX ix_{table_name}_search_vector
            ON {table_name} USING gin (search_vector)
            """
        )
        op.execute(
            f"""
            CREATE INDEX ix_{table_name}_embedding_hnsw
            ON {table_name} USING hnsw (embedding vector_cosine_ops)
            """
        )


def downgrade() -> None:
    for table_name in reversed(SEARCH_TABLES):
        op.drop_index(
            f"ix_{table_name}_embedding_hnsw",
            table_name=table_name,
        )
        op.drop_index(
            f"ix_{table_name}_search_vector",
            table_name=table_name,
        )
        op.drop_column(table_name, "search_vector")
