"""Create goal/plan/completion tables (17).

显式目标、计划图与完成条件：goals / acceptance_criteria / plan_versions /
plan_steps / plan_edges / step_evidence / completion_assessments；
agent_runs 增加 goal_id / plan_version_id / step_id 关联。

Revision ID: 20260823_0038
Revises: 20260823_0037
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0038"
down_revision: str | None = "20260823_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("scope", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("constraints", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft", index=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(32), nullable=False, server_default="user"),
        sa.Column("cancelled_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "acceptance_criteria",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("goal_id", sa.String(36), sa.ForeignKey("goals.id"), nullable=False, index=True),
        sa.Column("criterion_type", sa.String(32), nullable=False, server_default="artifact_exists"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("target", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("evidence_requirement", sa.String(32), nullable=False, server_default="required"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "plan_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("goal_id", sa.String(36), sa.ForeignKey("goals.id"), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("planner", sa.String(32), nullable=False, server_default="deterministic"),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("goal_id", "version", name="uq_plan_version_goal"),
    )

    op.create_table(
        "plan_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_version_id", sa.String(36), sa.ForeignKey("plan_versions.id"), nullable=False, index=True),
        sa.Column("step_key", sa.String(100), nullable=False, server_default=""),
        sa.Column("task", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent_capability", sa.String(100), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending", index=True),
        sa.Column("budget_max_cost", sa.Float(), nullable=False, server_default="5"),
        sa.Column("max_turns", sa.Integer(), nullable=False, server_default="16"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("declared_by", sa.String(32), nullable=False, server_default="planner"),
        sa.Column("completion_declared_by", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_plan_steps_plan_status", "plan_steps", ["plan_version_id", "status"])

    op.create_table(
        "plan_edges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_version_id", sa.String(36), sa.ForeignKey("plan_versions.id"), nullable=False, index=True),
        sa.Column("source_step_key", sa.String(100), nullable=False, server_default=""),
        sa.Column("target_step_key", sa.String(100), nullable=False, server_default=""),
        sa.Column("edge_type", sa.String(24), nullable=False, server_default="dependency"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "plan_version_id", "source_step_key", "target_step_key",
            name="uq_plan_edge",
        ),
    )

    op.create_table(
        "step_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("step_id", sa.String(36), sa.ForeignKey("plan_steps.id"), nullable=False, index=True),
        sa.Column("evidence_type", sa.String(32), nullable=False, server_default="artifact"),
        sa.Column("ref_id", sa.String(200), nullable=False, server_default=""),
        sa.Column("ref_kind", sa.String(64), nullable=False, server_default=""),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "completion_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("goal_id", sa.String(36), sa.ForeignKey("goals.id"), nullable=False, index=True),
        sa.Column("plan_version_id", sa.String(36), sa.ForeignKey("plan_versions.id"), nullable=False),
        sa.Column("verifier", sa.String(32), nullable=False, server_default="deterministic"),
        sa.Column("result", sa.String(32), nullable=False, server_default="insufficient_evidence"),
        sa.Column("criterion_results", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("gaps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("goal_id", "plan_version_id", name="uq_assessment_goal_plan"),
    )

    op.add_column(
        "agent_runs",
        sa.Column("goal_id", sa.String(36), sa.ForeignKey("goals.id"), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("plan_version_id", sa.String(36), sa.ForeignKey("plan_versions.id"), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("step_id", sa.String(36), sa.ForeignKey("plan_steps.id"), nullable=True),
    )
    op.create_index("ix_agent_runs_goal_id", "agent_runs", ["goal_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_goal_id", table_name="agent_runs")
    op.drop_column("agent_runs", "step_id")
    op.drop_column("agent_runs", "plan_version_id")
    op.drop_column("agent_runs", "goal_id")
    op.drop_table("completion_assessments")
    op.drop_table("step_evidence")
    op.drop_table("plan_edges")
    op.drop_table("plan_steps")
    op.drop_table("plan_versions")
    op.drop_table("acceptance_criteria")
    op.drop_table("goals")
