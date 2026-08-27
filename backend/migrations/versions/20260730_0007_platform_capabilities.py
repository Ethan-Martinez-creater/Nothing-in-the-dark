"""Track real platform smoke-test readiness.

Revision ID: 20260730_0007
Revises: 20260730_0006
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0007"
down_revision: str | None = "20260730_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORMS = ("weibo", "bilibili", "tieba", "zhihu", "douyin")


def upgrade() -> None:
    op.create_table(
        "platform_capabilities",
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="validation_required",
        ),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("platform"),
    )
    op.create_index(
        "ix_platform_capabilities_status",
        "platform_capabilities",
        ["status"],
    )
    capability_table = sa.table(
        "platform_capabilities",
        sa.column("platform", sa.String()),
        sa.column("status", sa.String()),
        sa.column("checks", sa.JSON()),
        sa.column("last_error", sa.Text()),
        sa.column("verified_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        capability_table,
        [
            {
                "platform": platform,
                "status": "validation_required",
                "checks": {},
                "last_error": None,
                "verified_at": None,
                "updated_at": datetime.now(UTC),
            }
            for platform in PLATFORMS
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_capabilities_status",
        table_name="platform_capabilities",
    )
    op.drop_table("platform_capabilities")
