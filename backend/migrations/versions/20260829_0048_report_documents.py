"""M7: product-layer report documents.

Revision ID: 20260829_0048
Revises: 20260829_0047
"""

import sqlalchemy as sa
from alembic import op

revision = "20260829_0048"
down_revision = "20260829_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("source_artifact_id", sa.String(length=36), nullable=False),
        sa.Column("supersedes_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name="fk_report_doc_case"),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"], ["artifacts.id"], name="fk_report_doc_artifact"
        ),
    )
    op.create_index(
        "ix_report_documents_case_id", "report_documents", ["case_id"]
    )
    op.create_index(
        "ix_report_documents_case_status", "report_documents", ["case_id", "status"]
    )
    op.create_index(
        "ix_report_documents_family", "report_documents", ["family_id", "created_at"]
    )
    op.create_index(
        "ix_report_documents_source_artifact_id",
        "report_documents",
        ["source_artifact_id"],
    )


def downgrade() -> None:
    for index_name in (
        "ix_report_documents_source_artifact_id",
        "ix_report_documents_family",
        "ix_report_documents_case_status",
        "ix_report_documents_case_id",
    ):
        op.drop_index(index_name, table_name="report_documents")
    op.drop_table("report_documents")
