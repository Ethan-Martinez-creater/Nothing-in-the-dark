"""versioned alignment candidates and integrity review audit fields

Revision ID: 20260821_0031
Revises: 20260821_0030
"""

import sqlalchemy as sa
from alembic import op

revision = "20260821_0031"
down_revision = "20260821_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_alignment_candidate_case_keys_relation",
        "alignment_candidates",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_alignment_candidate_case_keys_relation_version",
        "alignment_candidates",
        ["case_id", "left_key", "right_key", "relation_type", "model_version"],
    )
    op.add_column(
        "risk_assessments",
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "risk_assessments",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("risk_assessments", "reviewed_at")
    op.drop_column("risk_assessments", "review_note")
    op.drop_constraint(
        "uq_alignment_candidate_case_keys_relation_version",
        "alignment_candidates",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_alignment_candidate_case_keys_relation",
        "alignment_candidates",
        ["case_id", "left_key", "right_key", "relation_type"],
    )
