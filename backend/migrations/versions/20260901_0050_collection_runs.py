"""Async progressive collection: durable collection_runs table.

Revision ID: 20260901_0050
Revises: 20260830_0049
"""

import sqlalchemy as sa
from alembic import op

revision = "20260901_0050"
down_revision = "20260830_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collection_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("collection_definition_id", sa.String(length=36), nullable=True),
        sa.Column("collection_definition_version", sa.Integer(), nullable=True),
        sa.Column("trigger_run_id", sa.String(length=36), nullable=True),
        sa.Column("trigger_turn_id", sa.String(length=36), nullable=True),
        sa.Column("trigger_tool_call_id", sa.String(length=36), nullable=True),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("phase", sa.String(length=16), nullable=False, server_default="discovery"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("request_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("progress_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("posts_collected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments_collected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"],
            name="fk_collection_runs_case", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "case_id", "idempotency_key", name="uq_collection_run_idem"
        ),
    )
    op.create_index(
        "ix_collection_runs_status_created",
        "collection_runs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_collection_runs_case_created",
        "collection_runs",
        ["case_id", "created_at"],
    )
    op.create_index(
        "ix_collection_runs_lease_expires",
        "collection_runs",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_collection_runs_fingerprint",
        "collection_runs",
        ["request_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_collection_runs_fingerprint", table_name="collection_runs")
    op.drop_index("ix_collection_runs_lease_expires", table_name="collection_runs")
    op.drop_index("ix_collection_runs_case_created", table_name="collection_runs")
    op.drop_index("ix_collection_runs_status_created", table_name="collection_runs")
    op.drop_table("collection_runs")
