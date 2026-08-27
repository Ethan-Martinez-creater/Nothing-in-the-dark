"""Atomic per-minute share download rate limiting.

Revision ID: 20260824_0045
Revises: 20260823_0044
"""

import sqlalchemy as sa
from alembic import op

revision = "20260824_0045"
down_revision = "20260823_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "share_links",
        sa.Column("download_window_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "share_links",
        sa.Column("download_window_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("share_links", "download_window_count")
    op.drop_column("share_links", "download_window_started_at")
