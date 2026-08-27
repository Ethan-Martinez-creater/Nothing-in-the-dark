"""Persist cache-aware model pricing details.

Revision ID: 20260730_0005
Revises: 20260730_0004
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0005"
down_revision: str | None = "20260730_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_calls",
        sa.Column(
            "cached_input_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "model_calls",
        sa.Column(
            "currency",
            sa.String(length=8),
            nullable=False,
            server_default="CNY",
        ),
    )
    op.add_column(
        "model_calls",
        sa.Column("pricing_model", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_calls", "pricing_model")
    op.drop_column("model_calls", "currency")
    op.drop_column("model_calls", "cached_input_tokens")
