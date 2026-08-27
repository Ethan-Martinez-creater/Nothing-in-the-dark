"""Create content-security assessment and guardrail tables (16).

不可信内容与 Agent 注入防御：content_security_assessments /
guardrail_decisions。

Revision ID: 20260823_0037
Revises: 20260822_0036
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0037"
down_revision: str | None = "20260822_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "content_security_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("object_type", sa.String(32), nullable=False, server_default="content"),
        sa.Column("object_id", sa.String(200), nullable=False, server_default=""),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=True, index=True),
        sa.Column("trust_level", sa.String(32), nullable=False, server_default="external_content"),
        sa.Column("classification", sa.String(64), nullable=False, server_default="general"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("risk_signals", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("detector", sa.String(100), nullable=False, server_default=""),
        sa.Column("detector_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("disposition", sa.String(32), nullable=False, server_default="allowed"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("source_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("review_state", sa.String(32), nullable=False, server_default="unreviewed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_content_security_trust",
        "content_security_assessments",
        ["trust_level"],
    )
    op.create_index(
        "ix_content_security_object",
        "content_security_assessments",
        ["object_type", "object_id"],
    )

    op.create_table(
        "guardrail_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stage", sa.String(32), nullable=False, index=True),
        sa.Column("run_id", sa.String(36), nullable=True, index=True),
        sa.Column("turn_id", sa.String(64), nullable=True),
        sa.Column("tool_call_id", sa.String(100), nullable=True, index=True),
        sa.Column("tool", sa.String(160), nullable=True),
        sa.Column("decision", sa.String(32), nullable=False, server_default="allow"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("policy_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("signal_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_guardrail_decision_created",
        "guardrail_decisions",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("guardrail_decisions")
    op.drop_table("content_security_assessments")
