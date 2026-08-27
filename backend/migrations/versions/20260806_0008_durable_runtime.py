"""Durable runtime: tool-call audit, approvals and worker leases.

Revision ID: 20260806_0008
Revises: 20260730_0007
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0008"
down_revision: str | None = "20260730_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # tool_calls: full audit trail, idempotency and approval linkage.
    op.add_column(
        "tool_calls",
        sa.Column("input_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("output_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tool_calls",
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tool_calls",
        sa.Column("estimated_cost", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tool_calls",
        sa.Column("idempotency_key", sa.String(100), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("approval_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_tool_calls_idempotency_key",
        "tool_calls",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_tool_calls_run_id_status",
        "tool_calls",
        ["run_id", "status"],
    )

    # agent_runs: worker lease so a crashed worker cannot deadlock a run.
    op.add_column(
        "agent_runs",
        sa.Column("lease_owner", sa.String(100), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_runs_status_lease_expires_at",
        "agent_runs",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_status_lease_expires_at", table_name="agent_runs")
    op.drop_column("agent_runs", "lease_expires_at")
    op.drop_column("agent_runs", "lease_owner")
    op.drop_index("ix_tool_calls_run_id_status", table_name="tool_calls")
    op.drop_index("ix_tool_calls_idempotency_key", table_name="tool_calls")
    op.drop_column("tool_calls", "approval_id")
    op.drop_column("tool_calls", "idempotency_key")
    op.drop_column("tool_calls", "estimated_cost")
    op.drop_column("tool_calls", "duration_ms")
    op.drop_column("tool_calls", "retry_count")
    op.drop_column("tool_calls", "output_summary")
    op.drop_column("tool_calls", "input_summary")
