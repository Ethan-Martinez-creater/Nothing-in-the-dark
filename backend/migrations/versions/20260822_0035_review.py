"""Create review workbench tables (09).

分层人工调查与裁决工作台：review_items / review_assignments /
review_decisions / review_comments / review_policies / case_activity_log。

Revision ID: 20260822_0035
Revises: 20260822_0034
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0035"
down_revision: str | None = "20260822_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("object_type", sa.String(32), nullable=False, index=True),
        sa.Column("object_id", sa.String(200), nullable=False, index=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="unreviewed", index=True),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="low"),
        sa.Column("queue", sa.String(64), nullable=False, server_default="default"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "object_type", "object_id", name="uq_review_item_object"),
    )

    op.create_table(
        "review_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("review_items.id"), nullable=False, index=True),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
    )

    op.create_table(
        "review_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("review_items.id"), nullable=False, index=True),
        sa.Column("object_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("structured_patch", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(100), nullable=False, server_default="local_operator"),
        sa.Column("supersedes_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "review_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("review_items.id"), nullable=False, index=True),
        sa.Column("thread_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("reference", sa.String(200), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="team"),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("actor", sa.String(100), nullable=False, server_default="local_operator"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "review_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("object_type", sa.String(32), nullable=False, index=True),
        sa.Column("risk_condition", sa.String(32), nullable=False, server_default="high"),
        sa.Column("required_reviews", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("allowed_actions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="172800"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("object_type", "risk_condition", name="uq_review_policy"),
    )

    op.create_table(
        "case_activity_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("activity_type", sa.String(48), nullable=False, index=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(100), nullable=False, server_default="system"),
        sa.Column("ref_run_id", sa.String(36), nullable=True),
        sa.Column("ref_tool_call_id", sa.String(100), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("case_activity_log")
    op.drop_table("review_policies")
    op.drop_table("review_comments")
    op.drop_table("review_decisions")
    op.drop_table("review_assignments")
    op.drop_table("review_items")
