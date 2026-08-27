"""Create narrative tables (10).

叙事生命周期与纠错传播评估：narratives / narrative_versions /
narrative_claims / narrative_posts / narrative_transitions /
correction_events / lifecycle_snapshots / correction_impact_analyses。

Revision ID: 20260822_0034
Revises: 20260822_0033
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0034"
down_revision: str | None = "20260822_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "narratives",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("canonical_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_source", sa.String(32), nullable=False, server_default="clusterer"),
        sa.Column("review_state", sa.String(24), nullable=False, server_default="unreviewed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "narrative_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("narrative_id", sa.String(36), sa.ForeignKey("narratives.id"), nullable=False, index=True),
        sa.Column("data_watermark", sa.DateTime(timezone=True), nullable=True),
        sa.Column("algorithm_version", sa.String(64), nullable=False, server_default="1.0.0"),
        sa.Column("centroid", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("keywords", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("narrative_id", "algorithm_version", name="uq_narrative_version"),
    )

    op.create_table(
        "narrative_claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("narrative_id", sa.String(36), sa.ForeignKey("narratives.id"), nullable=False, index=True),
        sa.Column("claim_id", sa.String(36), nullable=False, index=True),
        sa.Column("membership_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("relation", sa.String(32), nullable=False, server_default="member"),
        sa.Column("decision_source", sa.String(32), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("narrative_id", "claim_id", name="uq_narrative_claim"),
    )

    op.create_table(
        "narrative_posts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("narrative_id", sa.String(36), sa.ForeignKey("narratives.id"), nullable=False, index=True),
        sa.Column("post_id", sa.String(36), nullable=False, index=True),
        sa.Column("membership_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("decision_source", sa.String(32), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("narrative_id", "post_id", name="uq_narrative_post"),
    )

    op.create_table(
        "narrative_transitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("narrative_id", sa.String(36), sa.ForeignKey("narratives.id"), nullable=False, index=True),
        sa.Column("from_variant", sa.String(200), nullable=False, server_default=""),
        sa.Column("to_variant", sa.String(200), nullable=False, server_default=""),
        sa.Column("transition_type", sa.String(32), nullable=False, server_default="variant_added"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "correction_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("source_post_id", sa.String(36), nullable=True),
        sa.Column("claim_id", sa.String(36), nullable=True),
        sa.Column("target_narrative_id", sa.String(36), nullable=True, index=True),
        sa.Column("correction_type", sa.String(32), nullable=False, server_default="clarification"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("publisher_class", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("review_state", sa.String(24), nullable=False, server_default="unreviewed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "lifecycle_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("narrative_id", sa.String(36), sa.ForeignKey("narratives.id"), nullable=False, index=True),
        sa.Column("time_bucket", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("platform", sa.String(32), nullable=False, server_default=""),
        sa.Column("volume", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_accounts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engagement", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risk_adjusted_metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("stage", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("narrative_id", "time_bucket", "platform", name="uq_lifecycle_snapshot"),
    )

    op.create_table(
        "correction_impact_analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("correction_event_id", sa.String(36), nullable=False, index=True),
        sa.Column("narrative_id", sa.String(36), nullable=True),
        sa.Column("window", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("method", sa.String(64), nullable=False, server_default="descriptive"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("limitations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("result", sa.String(200), nullable=False, server_default=""),
        sa.Column("confidence_level", sa.String(24), nullable=False, server_default="low"),
        sa.Column("causal_claim", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("correction_impact_analyses")
    op.drop_table("lifecycle_snapshots")
    op.drop_table("correction_events")
    op.drop_table("narrative_transitions")
    op.drop_table("narrative_posts")
    op.drop_table("narrative_claims")
    op.drop_table("narrative_versions")
    op.drop_table("narratives")
