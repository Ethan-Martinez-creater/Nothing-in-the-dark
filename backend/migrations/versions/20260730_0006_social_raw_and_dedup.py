"""Add raw social records and make normalized deduplication case scoped.

Revision ID: 20260730_0006
Revises: 20260730_0005
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0006"
down_revision: str | None = "20260730_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "source_posts_platform_native_id_key",
        "source_posts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_source_posts_case_platform_native",
        "source_posts",
        ["case_id", "platform", "native_id"],
    )
    op.drop_constraint(
        "source_comments_platform_native_id_key",
        "source_comments",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_source_comments_post_platform_native",
        "source_comments",
        ["post_id", "platform", "native_id"],
    )
    op.create_table(
        "raw_social_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("record_type", sa.String(length=32), nullable=False),
        sa.Column("native_id", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "case_id",
            "platform",
            "record_type",
            "native_id",
            "checksum",
            name="uq_raw_social_identity_checksum",
        ),
    )
    op.create_index(
        "ix_raw_social_records_case_id",
        "raw_social_records",
        ["case_id"],
    )
    op.create_index(
        "ix_raw_social_records_platform",
        "raw_social_records",
        ["platform"],
    )
    op.create_index(
        "ix_raw_social_records_record_type",
        "raw_social_records",
        ["record_type"],
    )
    op.create_index(
        "ix_raw_social_records_checksum",
        "raw_social_records",
        ["checksum"],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_social_records_checksum", table_name="raw_social_records")
    op.drop_index(
        "ix_raw_social_records_record_type",
        table_name="raw_social_records",
    )
    op.drop_index("ix_raw_social_records_platform", table_name="raw_social_records")
    op.drop_index("ix_raw_social_records_case_id", table_name="raw_social_records")
    op.drop_table("raw_social_records")
    op.drop_constraint(
        "uq_source_comments_post_platform_native",
        "source_comments",
        type_="unique",
    )
    op.create_unique_constraint(
        "source_comments_platform_native_id_key",
        "source_comments",
        ["platform", "native_id"],
    )
    op.drop_constraint(
        "uq_source_posts_case_platform_native",
        "source_posts",
        type_="unique",
    )
    op.create_unique_constraint(
        "source_posts_platform_native_id_key",
        "source_posts",
        ["platform", "native_id"],
    )
