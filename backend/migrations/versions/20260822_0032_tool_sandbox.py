"""Create tool sandbox / egress / secret tables (15).

工具沙箱、网络出口与密钥治理：tool_policy_versions /
tool_execution_profiles / secret_references / sandbox_executions /
egress_audit_events。

Revision ID: 20260822_0032
Revises: 20260821_0031
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0032"
down_revision: str | None = "20260821_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_policy_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="enforce"),
        sa.Column("rules_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("version", name="uq_tool_policy_version"),
    )

    op.create_table(
        "tool_execution_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("execution_class", sa.String(24), nullable=False, server_default="trusted_in_process"),
        sa.Column("filesystem", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("network", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("secrets", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("resources", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="low"),
        sa.Column("approval_policy", sa.String(32), nullable=False, server_default="none"),
        sa.Column("side_effects", sa.String(64), nullable=False, server_default="none"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.String(64), nullable=False, server_default="1.0.0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tool_name", name="uq_tool_execution_profile_tool"),
    )

    op.create_table(
        "secret_references",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="env"),
        sa.Column("ref", sa.String(200), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("version", sa.String(64), nullable=False, server_default="1"),
        sa.Column("rotation_state", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_secret_reference_name"),
    )

    op.create_table(
        "sandbox_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tool_call_id", sa.String(100), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("execution_class", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("resource_usage", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("termination_reason", sa.String(64), nullable=True),
        sa.Column("policy_version", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_sandbox_executions_tool_call_id", "sandbox_executions", ["tool_call_id"])
    op.create_index("ix_sandbox_executions_run_id", "sandbox_executions", ["run_id"])

    op.create_table(
        "egress_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tool_call_id", sa.String(100), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("host", sa.String(200), nullable=False, server_default=""),
        sa.Column("decision", sa.String(16), nullable=False, server_default="deny"),
        sa.Column("reason", sa.String(200), nullable=False, server_default=""),
        sa.Column("bytes_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes_received", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_egress_audit_events_tool_call_id", "egress_audit_events", ["tool_call_id"])


def downgrade() -> None:
    op.drop_table("egress_audit_events")
    op.drop_table("sandbox_executions")
    op.drop_table("secret_references")
    op.drop_table("tool_execution_profiles")
    op.drop_table("tool_policy_versions")
