"""Create uncertainty & bias tables (08).

不确定性、样本偏差与替代解释：quality_assessments /
analysis_assumptions / sensitivity_runs / alternative_hypotheses /
conclusion_confidence。

Revision ID: 20260820_0024
Revises: 20260820_0023
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0024"
down_revision: str | None = "20260820_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quality_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(200), nullable=False),
        sa.Column("dimension", sa.String(64), nullable=False),
        sa.Column("level", sa.String(32), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("method", sa.String(100), nullable=False, server_default=""),
        sa.Column("inputs", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("limitations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("version", sa.String(64), nullable=False, server_default="1.0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "target_type", "target_id", "dimension", "version", name="uq_quality_assessment_target_dim_version"),
    )

    op.create_table(
        "analysis_assumptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("analysis_target", sa.String(200), nullable=False),
        sa.Column("assumption_name", sa.String(200), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(100), nullable=False, server_default="system"),
        sa.Column("editable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "analysis_target", "assumption_name", name="uq_analysis_assumption_target_name"),
    )

    op.create_table(
        "sensitivity_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("baseline_hash", sa.String(64), nullable=False),
        sa.Column("baseline_params", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("variant_params", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("output_diff", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="completed", index=True),
        sa.Column("cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "baseline_hash", name="uq_sensitivity_run_case_hash"),
    )

    op.create_table(
        "alternative_hypotheses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("prediction", sa.Text(), nullable=False, server_default=""),
        sa.Column("supporting_evidence", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("opposing_evidence", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed", index=True),
        sa.Column("proposer", sa.String(100), nullable=False, server_default="system"),
        sa.Column("review_notes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "conclusion_confidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("conclusion_id", sa.String(200), nullable=False),
        sa.Column("conclusion_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("dimensions", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("final_level", sa.String(32), nullable=False),
        sa.Column("forbidden_reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("calibration_version", sa.String(64), nullable=False, server_default="uncalibrated"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "conclusion_id", "calibration_version", name="uq_conclusion_confidence_case_conclusion_cal"),
    )


def downgrade() -> None:
    op.drop_table("conclusion_confidence")
    op.drop_table("alternative_hypotheses")
    op.drop_table("sensitivity_runs")
    op.drop_table("analysis_assumptions")
    op.drop_table("quality_assessments")
