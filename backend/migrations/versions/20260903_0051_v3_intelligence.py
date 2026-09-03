"""V3 Intelligence schema: quality / workspace entity / cross link / derived signal.

Revision ID: 20260903_0051
Revises: 20260901_0050

V3 plan §5: 只创建 Schema / Index，不做任何历史数据回填或检测运行。
JSON columns use ``sa.Text()`` with ``server_default`` following the 0050
convention (models layer JSON is stored as TEXT on every dialect).
"""

import sqlalchemy as sa
from alembic import op

revision = "20260903_0051"
down_revision = "20260901_0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- investigation_quality (V3 §6) -------------------------------------
    op.create_table(
        "investigation_quality",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column(
            "grade", sa.String(length=24), nullable=False,
            server_default="insufficient_data",
        ),
        sa.Column("dimensions_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("metrics_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("gaps_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("warnings_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"],
            name="fk_investigation_quality_case", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("case_id", name="uq_investigation_quality_case"),
    )
    op.create_index("ix_investigation_quality_case_id", "investigation_quality", ["case_id"])
    op.create_index("ix_investigation_quality_grade", "investigation_quality", ["grade"])
    op.create_index(
        "ix_investigation_quality_input_fingerprint",
        "investigation_quality",
        ["input_fingerprint"],
    )
    op.create_index(
        "ix_investigation_quality_updated_at", "investigation_quality", ["updated_at"]
    )

    # --- workspace_entities (V3 §7) ----------------------------------------
    op.create_table(
        "workspace_entities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("entity_type", sa.String(length=32), nullable=False, server_default="account"),
        sa.Column("canonical_name", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("aliases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("attributes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False, server_default="system"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_workspace_entities_entity_type", "workspace_entities", ["entity_type"])
    op.create_index("ix_workspace_entities_status", "workspace_entities", ["status"])

    # --- workspace_entity_keys (V3 §8) -------------------------------------
    op.create_table(
        "workspace_entity_keys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("key_type", sa.String(length=32), nullable=False),
        sa.Column("key_value", sa.String(length=500), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("method", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["workspace_entities.id"],
            name="fk_workspace_entity_keys_entity", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("key_type", "key_value", name="uq_workspace_entity_key"),
    )
    op.create_index("ix_workspace_entity_keys_entity_id", "workspace_entity_keys", ["entity_id"])

    # --- workspace_entity_case_links (V3 §9) -------------------------------
    op.create_table(
        "workspace_entity_case_links",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("method", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["workspace_entities.id"],
            name="fk_workspace_entity_case_links_entity", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"],
            name="fk_workspace_entity_case_links_case", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "case_id", "source_type", "source_id", name="uq_workspace_entity_case_link"
        ),
    )
    op.create_index(
        "ix_workspace_entity_case_links_entity_case",
        "workspace_entity_case_links",
        ["entity_id", "case_id"],
    )
    op.create_index(
        "ix_workspace_entity_case_links_case",
        "workspace_entity_case_links",
        ["case_id"],
    )

    # --- workspace_entity_relations (V3 §9.1) ------------------------------
    op.create_table(
        "workspace_entity_relations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("left_entity_id", sa.String(length=36), nullable=False),
        sa.Column("right_entity_id", sa.String(length=36), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False, server_default="same_as"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("source_case_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("method", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["left_entity_id"], ["workspace_entities.id"],
            name="fk_workspace_entity_relations_left", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["right_entity_id"], ["workspace_entities.id"],
            name="fk_workspace_entity_relations_right", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_case_id"], ["cases.id"],
            name="fk_workspace_entity_relations_case", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "source_case_id",
            "left_entity_id",
            "right_entity_id",
            "relation_type",
            name="uq_workspace_entity_relation",
        ),
    )
    op.create_index(
        "ix_workspace_entity_relations_left_status",
        "workspace_entity_relations",
        ["left_entity_id", "status"],
    )
    op.create_index(
        "ix_workspace_entity_relations_right_status",
        "workspace_entity_relations",
        ["right_entity_id", "status"],
    )
    op.create_index(
        "ix_workspace_entity_relations_case_status",
        "workspace_entity_relations",
        ["source_case_id", "status"],
    )

    # --- cross_investigation_links (V3 §10) --------------------------------
    op.create_table(
        "cross_investigation_links",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("left_case_id", sa.String(length=36), nullable=False),
        sa.Column("right_case_id", sa.String(length=36), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("feature_scores_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["left_case_id"], ["cases.id"],
            name="fk_cross_investigation_links_left_case", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["right_case_id"], ["cases.id"],
            name="fk_cross_investigation_links_right_case", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "fingerprint", name="uq_cross_investigation_link_fingerprint"
        ),
    )
    op.create_index(
        "ix_cross_investigation_links_left_active",
        "cross_investigation_links",
        ["left_case_id", "is_active"],
    )
    op.create_index(
        "ix_cross_investigation_links_right_active",
        "cross_investigation_links",
        ["right_case_id", "is_active"],
    )
    op.create_index(
        "ix_cross_investigation_links_type_status_active",
        "cross_investigation_links",
        ["relation_type", "status", "is_active"],
    )

    # --- derived_signals (V3 §11) ------------------------------------------
    op.create_table(
        "derived_signals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=200), nullable=False),
        sa.Column("signal_type", sa.String(length=48), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("detector_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("why_it_matters", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("metric_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("evidence_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("related_case_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("detector_version", sa.String(length=64), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"],
            name="fk_derived_signals_case", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("fingerprint", name="uq_derived_signal_fingerprint"),
    )
    op.create_index("ix_derived_signals_case_id", "derived_signals", ["case_id"])
    op.create_index("ix_derived_signals_signal_type", "derived_signals", ["signal_type"])
    op.create_index("ix_derived_signals_status", "derived_signals", ["status"])

    # --- derived_signal_case_links (V3 §11.1) ------------------------------
    op.create_table(
        "derived_signal_case_links",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("signal_id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"], ["derived_signals.id"],
            name="fk_derived_signal_case_links_signal", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"],
            name="fk_derived_signal_case_links_case", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("signal_id", "case_id", name="uq_derived_signal_case_link"),
    )
    op.create_index("ix_derived_signal_case_links_case", "derived_signal_case_links", ["case_id"])

    # --- source_posts composite index for cross-case matching (V3 §5) ------
    op.create_index(
        "ix_source_posts_content_hash_case",
        "source_posts",
        ["content_hash", "case_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_source_posts_content_hash_case", table_name="source_posts")

    op.drop_index(
        "ix_derived_signal_case_links_case", table_name="derived_signal_case_links"
    )
    op.drop_table("derived_signal_case_links")

    op.drop_index("ix_derived_signals_status", table_name="derived_signals")
    op.drop_index("ix_derived_signals_signal_type", table_name="derived_signals")
    op.drop_index("ix_derived_signals_case_id", table_name="derived_signals")
    op.drop_table("derived_signals")

    op.drop_index(
        "ix_cross_investigation_links_type_status_active", table_name="cross_investigation_links"
    )
    op.drop_index(
        "ix_cross_investigation_links_right_active", table_name="cross_investigation_links"
    )
    op.drop_index(
        "ix_cross_investigation_links_left_active", table_name="cross_investigation_links"
    )
    op.drop_table("cross_investigation_links")

    op.drop_index(
        "ix_workspace_entity_relations_case_status", table_name="workspace_entity_relations"
    )
    op.drop_index(
        "ix_workspace_entity_relations_right_status", table_name="workspace_entity_relations"
    )
    op.drop_index(
        "ix_workspace_entity_relations_left_status", table_name="workspace_entity_relations"
    )
    op.drop_table("workspace_entity_relations")

    op.drop_index(
        "ix_workspace_entity_case_links_case", table_name="workspace_entity_case_links"
    )
    op.drop_index(
        "ix_workspace_entity_case_links_entity_case", table_name="workspace_entity_case_links"
    )
    op.drop_table("workspace_entity_case_links")

    op.drop_index("ix_workspace_entity_keys_entity_id", table_name="workspace_entity_keys")
    op.drop_table("workspace_entity_keys")

    op.drop_index("ix_workspace_entities_status", table_name="workspace_entities")
    op.drop_index("ix_workspace_entities_entity_type", table_name="workspace_entities")
    op.drop_table("workspace_entities")

    op.drop_index("ix_investigation_quality_updated_at", table_name="investigation_quality")
    op.drop_index(
        "ix_investigation_quality_input_fingerprint", table_name="investigation_quality"
    )
    op.drop_index("ix_investigation_quality_grade", table_name="investigation_quality")
    op.drop_index("ix_investigation_quality_case_id", table_name="investigation_quality")
    op.drop_table("investigation_quality")
