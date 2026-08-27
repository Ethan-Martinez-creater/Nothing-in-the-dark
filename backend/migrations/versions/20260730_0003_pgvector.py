"""Enable pgvector and convert embedding columns to vector(1024).

Revision ID: 20260730_0003
Revises: 20260729_0002
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260730_0003"
down_revision: str | None = "20260729_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_TABLES = ("memories", "knowledge_chunks", "source_posts")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for table_name in EMBEDDING_TABLES:
        op.execute(
            f"""
            ALTER TABLE {table_name}
            ALTER COLUMN embedding TYPE vector(1024)
            USING CASE
                WHEN embedding IS NULL THEN NULL
                ELSE (embedding::text)::vector
            END
            """
        )


def downgrade() -> None:
    for table_name in EMBEDDING_TABLES:
        op.execute(
            f"""
            ALTER TABLE {table_name}
            ALTER COLUMN embedding TYPE jsonb
            USING CASE
                WHEN embedding IS NULL THEN NULL
                ELSE (embedding::text)::jsonb
            END
            """
        )
