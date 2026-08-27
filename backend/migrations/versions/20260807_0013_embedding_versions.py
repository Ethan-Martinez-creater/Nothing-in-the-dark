"""Embedding model version registry for RAG rebuild tracking.

Revision ID: 20260807_0013
Revises: 20260807_0012
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0013"
down_revision: str | None = "20260807_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "embedding_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("model_name", sa.String(length=300), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("dimensions", sa.Integer(), server_default="1024", nullable=False),
        sa.Column("record_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rebuilt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("model_version"),
    )
    op.create_index(
        "ix_embedding_versions_model_version",
        "embedding_versions",
        ["model_version"],
    )


def downgrade() -> None:
    op.drop_table("embedding_versions")
