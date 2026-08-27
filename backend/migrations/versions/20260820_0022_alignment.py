"""Create cross-platform alignment tables (06).

跨平台实体、内容与叙事对齐：canonical_entities / entity_mentions /
alignment_candidates / content_families / content_family_members /
narrative_memberships。对齐候选使用排序后的无向唯一键。

Revision ID: 20260820_0022
Revises: 20260820_0021
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0022"
down_revision: str | None = "20260820_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "canonical_entities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("entity_type", sa.String(32), nullable=False, index=True),
        sa.Column("canonical_name", sa.String(300), nullable=False, index=True),
        sa.Column("aliases", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed", index=True),
        sa.Column("created_by", sa.String(100), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "entity_type", "canonical_name", name="uq_canonical_entity_case_type_name"),
    )

    op.create_table(
        "entity_mentions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("canonical_entities.id"), nullable=False, index=True),
        sa.Column("platform_object_type", sa.String(32), nullable=False, server_default="post"),
        sa.Column("platform_object_id", sa.String(500), nullable=False),
        sa.Column("text_span", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("method", sa.String(64), nullable=False, server_default=""),
        sa.Column("version", sa.String(64), nullable=False, server_default="1.0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("entity_id", "platform_object_type", "platform_object_id", name="uq_entity_mention_entity_object"),
    )

    op.create_table(
        "alignment_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("left_type", sa.String(32), nullable=False),
        sa.Column("left_id", sa.String(500), nullable=False),
        sa.Column("right_type", sa.String(32), nullable=False),
        sa.Column("right_id", sa.String(500), nullable=False),
        sa.Column("left_key", sa.String(600), nullable=False),
        sa.Column("right_key", sa.String(600), nullable=False),
        sa.Column("relation_type", sa.String(32), nullable=False, server_default="same_as"),
        sa.Column("feature_scores", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("combined_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("decision", sa.String(32), nullable=False, server_default="pending", index=True),
        sa.Column("review_id", sa.String(36), nullable=True),
        sa.Column("model_version", sa.String(64), nullable=False, server_default="1.0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "left_key", "right_key", "relation_type", name="uq_alignment_candidate_case_keys_relation"),
    )

    op.create_table(
        "content_families",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("label", sa.String(300), nullable=False, server_default=""),
        sa.Column("earliest_known_id", sa.String(500), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="open", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "content_family_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(36), sa.ForeignKey("content_families.id"), nullable=False, index=True),
        sa.Column("member_type", sa.String(32), nullable=False, server_default="post"),
        sa.Column("member_id", sa.String(500), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False, server_default="original"),
        sa.Column("time_offset_ms", sa.Integer(), nullable=True),
        sa.Column("edit_features", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("decision_source", sa.String(100), nullable=False, server_default="algorithm"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("family_id", "member_id", name="uq_content_family_member_family_member"),
    )

    op.create_table(
        "narrative_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("narrative_id", sa.String(36), nullable=False, index=True),
        sa.Column("post_id", sa.String(36), sa.ForeignKey("source_posts.id"), nullable=True, index=True),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("claims.id"), nullable=True, index=True),
        sa.Column("membership_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("narrative_id", "post_id", name="uq_narrative_membership_narrative_post"),
    )


def downgrade() -> None:
    op.drop_table("narrative_memberships")
    op.drop_table("content_family_members")
    op.drop_table("content_families")
    op.drop_table("alignment_candidates")
    op.drop_table("entity_mentions")
    op.drop_table("canonical_entities")
