"""Extend approvals and add execution authorizations (21).

广义人工介入与反馈闭环：approvals 兼容扩展（审批类型/策略版本/风险/
作用域/编辑批准/过期/消费者/幂等版本/恢复 token/替代链），新增
execution_authorizations 一次性授权表。

Revision ID: 20260823_0039
Revises: 20260823_0038
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0039"
down_revision: str | None = "20260823_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("approvals", sa.Column("approval_type", sa.String(32), nullable=True))
    op.add_column("approvals", sa.Column("policy_version", sa.String(32), nullable=True))
    op.add_column("approvals", sa.Column("risk_level", sa.String(16), nullable=True))
    op.add_column("approvals", sa.Column("scope", sa.String(64), nullable=True))
    op.add_column("approvals", sa.Column("requested_action", sa.String(200), nullable=True))
    op.add_column("approvals", sa.Column("redacted_preview", sa.Text(), nullable=True))
    op.add_column("approvals", sa.Column("allowed_decisions", sa.JSON(), nullable=True))
    op.add_column("approvals", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("approvals", sa.Column("decision", sa.String(32), nullable=True))
    op.add_column("approvals", sa.Column("edited_action", sa.JSON(), nullable=True))
    op.add_column("approvals", sa.Column("actor", sa.String(100), nullable=True))
    op.add_column("approvals", sa.Column("decision_version", sa.String(32), nullable=True))
    op.add_column("approvals", sa.Column("resume_token_hash", sa.String(64), nullable=True))
    op.add_column("approvals", sa.Column("supersedes_id", sa.String(36), nullable=True))
    op.create_index("ix_approvals_type_status", "approvals", ["approval_type", "status"])
    op.create_index("ix_approvals_expires", "approvals", ["expires_at"])

    op.create_table(
        "execution_authorizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("approval_id", sa.String(36), sa.ForeignKey("approvals.id"), nullable=False, index=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False, index=True),
        sa.Column("tool_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("argument_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("token_hash", name="uq_auth_token_hash"),
    )
    op.create_index("ix_auth_run_consumed", "execution_authorizations", ["run_id", "consumed_at"])


def downgrade() -> None:
    op.drop_table("execution_authorizations")
    op.drop_index("ix_approvals_expires", table_name="approvals")
    op.drop_index("ix_approvals_type_status", table_name="approvals")
    op.drop_column("approvals", "supersedes_id")
    op.drop_column("approvals", "resume_token_hash")
    op.drop_column("approvals", "decision_version")
    op.drop_column("approvals", "actor")
    op.drop_column("approvals", "edited_action")
    op.drop_column("approvals", "decision")
    op.drop_column("approvals", "expires_at")
    op.drop_column("approvals", "allowed_decisions")
    op.drop_column("approvals", "redacted_preview")
    op.drop_column("approvals", "requested_action")
    op.drop_column("approvals", "scope")
    op.drop_column("approvals", "risk_level")
    op.drop_column("approvals", "policy_version")
    op.drop_column("approvals", "approval_type")
