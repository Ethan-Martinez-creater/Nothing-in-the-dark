"""Domain models for M7: accounts, media assets, entities, propagation
nodes, evaluations and cost summaries.

Revision ID: 20260807_0012
Revises: 20260806_0011
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0012"
down_revision: str | None = "20260806_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("cases.id")),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("native_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=300), server_default="", nullable=False),
        sa.Column(
            "normalized_name", sa.String(length=300), nullable=False, index=True
        ),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("follower_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("verified", sa.Boolean(), server_default="0", nullable=False),
        sa.Column(
            "is_authoritative", sa.Boolean(), server_default="0", nullable=False
        ),
        sa.Column("metadata_json", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("platform", "native_id"),
    )
    op.create_index("ix_accounts_case_id", "accounts", ["case_id"])
    op.create_index("ix_accounts_is_authoritative", "accounts", ["is_authoritative"])

    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column(
            "post_id", sa.String(length=36), sa.ForeignKey("source_posts.id")
        ),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("media_type", sa.String(length=32), server_default="image", nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False, index=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column("phash", sa.String(length=64), nullable=True),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("keyframe_urls", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("metadata_json", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("case_id", "normalized_url", "post_id"),
    )
    op.create_index("ix_media_assets_case_id", "media_assets", ["case_id"])
    op.create_index("ix_media_assets_post_id", "media_assets", ["post_id"])

    op.create_table(
        "entities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("aliases", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("mentions_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("case_id", "entity_type", "normalized_name"),
    )
    op.create_index("ix_entities_case_id", "entities", ["case_id"])
    op.create_index("ix_entities_entity_type", "entities", ["entity_type"])
    op.create_index("ix_entities_normalized_name", "entities", ["normalized_name"])

    op.create_table(
        "propagation_nodes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column(
            "post_id", sa.String(length=36), sa.ForeignKey("source_posts.id"), nullable=False
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Float(), server_default="0", nullable=False),
        sa.Column("attributes", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("case_id", "post_id", "role"),
    )
    op.create_index("ix_propagation_nodes_case_id", "propagation_nodes", ["case_id"])
    op.create_index("ix_propagation_nodes_post_id", "propagation_nodes", ["post_id"])
    op.create_index("ix_propagation_nodes_role", "propagation_nodes", ["role"])

    op.create_table(
        "evaluations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("cases.id")),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("agent_runs.id")),
        sa.Column("metric", sa.String(length=100), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("details", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_evaluations_case_id", "evaluations", ["case_id"])
    op.create_index("ix_evaluations_run_id", "evaluations", ["run_id"])
    op.create_index("ix_evaluations_metric", "evaluations", ["metric"])

    op.create_table(
        "cost_summaries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("summary_type", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("agent_runs.id"), unique=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("cases.id")),
        sa.Column("model_cost", sa.Float(), server_default="0", nullable=False),
        sa.Column("tool_cost", sa.Float(), server_default="0", nullable=False),
        sa.Column("total_cost", sa.Float(), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=8), server_default="CNY", nullable=False),
        sa.Column("period", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_cost_summaries_summary_type", "cost_summaries", ["summary_type"])
    op.create_index("ix_cost_summaries_case_id", "cost_summaries", ["case_id"])


def downgrade() -> None:
    op.drop_table("cost_summaries")
    op.drop_table("evaluations")
    op.drop_table("propagation_nodes")
    op.drop_table("entities")
    op.drop_table("media_assets")
    op.drop_table("accounts")
