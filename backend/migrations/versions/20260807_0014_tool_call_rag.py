"""Structured RAG hit summary column on tool_calls for the trace drawer.

Revision ID: 20260807_0014
Revises: 20260807_0013
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0014"
down_revision: str | None = "20260807_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_calls",
        sa.Column("rag", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tool_calls", "rag")
