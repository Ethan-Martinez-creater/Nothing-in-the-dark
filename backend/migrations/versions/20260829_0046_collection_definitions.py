"""M3: explicit versioned collection definitions.

Revision ID: 20260829_0046
Revises: 20260824_0045
"""

import sqlalchemy as sa
from alembic import op

revision = "20260829_0046"
down_revision = "20260824_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collection_definitions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("platforms", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("platform_queries", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("exclusions", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("filters", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("generated_by_run_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name="fk_collection_case"),
        sa.ForeignKeyConstraint(
            ["generated_by_run_id"], ["agent_runs.id"], name="fk_collection_run"
        ),
        sa.UniqueConstraint("case_id", "version", name="uq_collection_case_version"),
    )
    op.create_index(
        "ix_collection_definitions_case_id",
        "collection_definitions",
        ["case_id"],
    )
    # Partial unique：每个 case 至多一个 active（PG 与 SQLite 双方言 where）。
    op.create_index(
        "uq_collection_case_active",
        "collection_definitions",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_collection_case_active", table_name="collection_definitions")
    op.drop_index("ix_collection_definitions_case_id", table_name="collection_definitions")
    op.drop_table("collection_definitions")
