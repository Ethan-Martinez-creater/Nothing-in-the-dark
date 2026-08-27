"""analysis job recovery, idempotency, retry and cancellation

Revision ID: 20260821_0030
Revises: 20260820_0029
"""

import sqlalchemy as sa
from alembic import op

revision = "20260821_0030"
down_revision = "20260820_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analysis_jobs", sa.Column("idempotency_key", sa.String(64), nullable=True))
    op.add_column(
        "analysis_jobs", sa.Column("attempt", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "analysis_jobs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3")
    )
    op.add_column(
        "analysis_jobs", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "analysis_jobs",
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "analysis_jobs", sa.Column("progress_json", sa.JSON(), nullable=False, server_default="{}")
    )
    op.create_unique_constraint(
        "uq_analysis_jobs_case_type_idempotency",
        "analysis_jobs",
        ["case_id", "job_type", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_analysis_jobs_case_type_idempotency", "analysis_jobs", type_="unique")
    for column in (
        "progress_json",
        "cancel_requested",
        "next_retry_at",
        "max_attempts",
        "attempt",
        "idempotency_key",
    ):
        op.drop_column("analysis_jobs", column)
