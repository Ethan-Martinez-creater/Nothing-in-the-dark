"""Create semantics tables (11).

中文复杂语义与跨语言分析：lexicon_entries / semantic_annotations /
annotation_corrections / translation_segments / semantic_model_versions。

Revision ID: 20260822_0033
Revises: 20260822_0032
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0033"
down_revision: str | None = "20260822_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lexicon_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("term", sa.String(200), nullable=False),
        sa.Column("normalized", sa.String(200), nullable=False, server_default=""),
        sa.Column("meaning", sa.Text(), nullable=False, server_default=""),
        sa.Column("domain", sa.String(64), nullable=False, server_default="general"),
        sa.Column("platform", sa.String(32), nullable=False, server_default=""),
        sa.Column("language", sa.String(16), nullable=False, server_default="zh"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(200), nullable=False, server_default=""),
        sa.Column("review_state", sa.String(24), nullable=False, server_default="proposed"),
        sa.Column("version", sa.String(64), nullable=False, server_default="1.0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_lexicon_entries_term", "lexicon_entries", ["term"])
    op.create_index("ix_lexicon_entries_normalized", "lexicon_entries", ["normalized"])

    op.create_table(
        "semantic_annotations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("task", sa.String(32), nullable=False),
        sa.Column("label", sa.String(64), nullable=False, server_default=""),
        sa.Column("span_start", sa.Integer(), nullable=True),
        sa.Column("span_end", sa.Integer(), nullable=True),
        sa.Column("entity_ref", sa.String(200), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(32), nullable=False, server_default="rules"),
        sa.Column("model_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("lexicon_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "source_type", "source_id", "task", "label", "span_start",
            name="uq_semantic_annotation_key",
        ),
    )
    op.create_index("ix_semantic_annotations_source_id", "semantic_annotations", ["source_id"])
    op.create_index("ix_semantic_annotations_task", "semantic_annotations", ["task"])

    op.create_table(
        "annotation_corrections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("annotation_id", sa.String(36), nullable=False),
        sa.Column("original", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("corrected", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(100), nullable=False, server_default="local_operator"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_annotation_corrections_annotation_id", "annotation_corrections", ["annotation_id"])

    op.create_table(
        "translation_segments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("source_span", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_lang", sa.String(16), nullable=False, server_default="zh"),
        sa.Column("target_lang", sa.String(16), nullable=False, server_default="en"),
        sa.Column("translated_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("provider", sa.String(32), nullable=False, server_default="rules"),
        sa.Column("version", sa.String(64), nullable=False, server_default=""),
        sa.Column("quality_status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_translation_segments_source_id", "translation_segments", ["source_id"])

    op.create_table(
        "semantic_model_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("component", sa.String(32), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("capability", sa.String(64), nullable=False, server_default=""),
        sa.Column("training_data_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("eval_data_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("thresholds", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("component", "version", name="uq_semantic_model_version"),
    )


def downgrade() -> None:
    op.drop_table("semantic_model_versions")
    op.drop_table("translation_segments")
    op.drop_table("annotation_corrections")
    op.drop_table("semantic_annotations")
    op.drop_table("lexicon_entries")
