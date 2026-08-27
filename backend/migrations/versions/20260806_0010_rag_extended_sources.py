"""Extended RAG sources: comments, artifacts, claims, evidence.

Revision ID: 20260806_0010
Revises: 20260806_0009
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "20260806_0010"
down_revision: str | None = "20260806_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, search text expression) — mirrors 20260730_0004 for new sources.
SEARCH_COLUMNS = {
    "source_comments": "coalesce(content, '')",
    "artifacts": "coalesce(title, '') || ' ' || coalesce(data::text, '')",
    "claims": "coalesce(text, '')",
    "evidence": "coalesce(excerpt, '')",
}


def upgrade() -> None:
    for table_name, search_expression in SEARCH_COLUMNS.items():
        op.add_column(table_name, sa.Column("embedding", Vector(1024), nullable=True))
        op.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (to_tsvector('simple', {search_expression})) STORED
            """
        )
        op.execute(
            f"CREATE INDEX ix_{table_name}_search_vector "
            f"ON {table_name} USING gin (search_vector)"
        )
        op.execute(
            f"CREATE INDEX ix_{table_name}_embedding_hnsw "
            f"ON {table_name} USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    for table_name in reversed(SEARCH_COLUMNS):
        op.drop_index(f"ix_{table_name}_embedding_hnsw", table_name=table_name)
        op.drop_index(f"ix_{table_name}_search_vector", table_name=table_name)
        op.drop_column(table_name, "search_vector")
        op.drop_column(table_name, "embedding")
