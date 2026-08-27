"""Create integrity risk tables (07).

垃圾营销、机器人与协同行为识别：behavior_feature_snapshots /
risk_assessments / coordination_clusters / coordination_members /
risk_policy_versions。

Revision ID: 20260820_0023
Revises: 20260820_0022
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0023"
down_revision: str | None = "20260820_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "behavior_feature_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("subject_type", sa.String(32), nullable=False, server_default="account"),
        sa.Column("subject_id", sa.String(200), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("feature_name", sa.String(64), nullable=False),
        sa.Column("feature_value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("coverage", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("extract_version", sa.String(64), nullable=False, server_default="1.0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "subject_type", "subject_id", "window_start", "feature_name", name="uq_behavior_snapshot_subject_window_feature"),
    )

    op.create_table(
        "risk_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("subject_type", sa.String(32), nullable=False, server_default="account"),
        sa.Column("subject_id", sa.String(200), nullable=False),
        sa.Column("risk_type", sa.String(32), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("band", sa.String(16), nullable=False, server_default="low"),
        sa.Column("reason_codes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("model_version", sa.String(64), nullable=False, server_default="1.0.0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="signal_only", index=True),
        sa.Column("reviewed_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "subject_type", "subject_id", "risk_type", "model_version", name="uq_risk_assessment_subject_type_version"),
    )

    op.create_table(
        "coordination_clusters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("algorithm_version", sa.String(64), nullable=False, server_default="1.0.0"),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("review_status", sa.String(32), nullable=False, server_default="signal_only", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "coordination_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cluster_id", sa.String(36), sa.ForeignKey("coordination_clusters.id"), nullable=False, index=True),
        sa.Column("account_id", sa.String(200), nullable=False),
        sa.Column("membership_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("cluster_id", "account_id", name="uq_coordination_member_cluster_account"),
    )

    op.create_table(
        "risk_policy_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("thresholds", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("weights", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("platforms", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("risk_policy_versions")
    op.drop_table("coordination_members")
    op.drop_table("coordination_clusters")
    op.drop_table("risk_assessments")
    op.drop_table("behavior_feature_snapshots")
