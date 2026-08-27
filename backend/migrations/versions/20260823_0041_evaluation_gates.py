"""Create evaluation datasets, runs and release gates (20).

真实数据评测、回归门禁与在线质量监控：dataset_manifests /
dataset_examples / evaluator_definitions / evaluation_runs /
release_gates / evaluation_gate_results。

Revision ID: 20260823_0041
Revises: 20260823_0040
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0041"
down_revision: str | None = "20260823_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_manifests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("version", sa.String(32), nullable=False, server_default="1.0.0"),
        sa.Column("task", sa.String(64), nullable=False, index=True),
        sa.Column("source", sa.String(64), nullable=False, server_default=""),
        sa.Column("license", sa.String(64), nullable=False, server_default=""),
        sa.Column("time_range", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("platforms", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="1.0"),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("example_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("train_holdout", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", "version", name="uq_manifest_name_version"),
    )

    op.create_table(
        "dataset_examples",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("manifest_id", sa.String(36), sa.ForeignKey("dataset_manifests.id"), nullable=False, index=True),
        sa.Column("example_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("task", sa.String(64), nullable=False, server_default=""),
        sa.Column("input_ref", sa.String(500), nullable=False, server_default=""),
        sa.Column("input_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("gold", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("difficulty", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("label_disagreement", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("training_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("manifest_id", "example_id", name="uq_example_manifest_id"),
    )
    op.create_index("ix_examples_input_hash", "dataset_examples", ["input_hash"])

    op.create_table(
        "evaluator_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, index=True),
        sa.Column("version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("metric", sa.String(100), nullable=False, server_default=""),
        sa.Column("deterministic", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("thresholds", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("dependencies", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("suite", sa.String(64), nullable=False, index=True),
        sa.Column("candidate_version", sa.String(100), nullable=False, server_default=""),
        sa.Column("baseline_version", sa.String(100), nullable=False, server_default=""),
        sa.Column("dataset_manifest_id", sa.String(36), sa.ForeignKey("dataset_manifests.id"), nullable=False, index=True),
        sa.Column("commit", sa.String(64), nullable=False, server_default=""),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("environment", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending", index=True),
        sa.Column("results", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("aggregate", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("differences", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("error_samples", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "release_gates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, index=True),
        sa.Column("suite", sa.String(64), nullable=False, server_default=""),
        sa.Column("thresholds", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("relative_regression_limits", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("mandatory", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "evaluation_gate_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("gate_id", sa.String(36), sa.ForeignKey("release_gates.id"), nullable=False, index=True),
        sa.Column("evaluation_run_id", sa.String(36), sa.ForeignKey("evaluation_runs.id"), nullable=False, index=True),
        sa.Column("decision", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("exempted_by", sa.String(100), nullable=True),
        sa.Column("exempt_reason", sa.Text(), nullable=True),
        sa.Column("exempt_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("gate_id", "evaluation_run_id", name="uq_gate_run"),
    )


def downgrade() -> None:
    op.drop_table("evaluation_gate_results")
    op.drop_table("release_gates")
    op.drop_table("evaluation_runs")
    op.drop_table("evaluator_definitions")
    op.drop_table("dataset_examples")
    op.drop_table("dataset_manifests")
