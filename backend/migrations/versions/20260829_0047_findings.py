"""M4: findings, finding_evidence_links, finding_source_links.

Revision ID: 20260829_0047
Revises: 20260829_0046
"""

import sqlalchemy as sa
from alembic import op

revision = "20260829_0047"
down_revision = "20260829_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "findings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="candidate"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("attributes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("source_run_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name="fk_findings_case"),
        sa.ForeignKeyConstraint(
            ["source_run_id"], ["agent_runs.id"], name="fk_findings_run"
        ),
    )
    op.create_index("ix_findings_case_id", "findings", ["case_id"])
    op.create_index("ix_findings_case_status", "findings", ["case_id", "status"])
    op.create_index("ix_findings_case_kind", "findings", ["case_id", "kind"])
    op.create_index("ix_findings_source_run_id", "findings", ["source_run_id"])

    op.create_table(
        "finding_evidence_links",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("finding_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_ref", sa.String(length=200), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["findings.id"], name="fk_finding_evidence_finding"
        ),
        sa.UniqueConstraint(
            "finding_id", "evidence_ref", "relation", name="uq_finding_evidence"
        ),
    )
    op.create_index(
        "ix_finding_evidence_links_finding_id",
        "finding_evidence_links",
        ["finding_id"],
    )

    op.create_table(
        "finding_source_links",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("finding_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=200), nullable=False),
        sa.Column("source_path", sa.String(length=200), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["findings.id"], name="fk_finding_source_finding"
        ),
        sa.UniqueConstraint(
            "source_type", "source_id", "source_path", name="uq_finding_source"
        ),
    )
    op.create_index(
        "ix_finding_source_links_finding_id", "finding_source_links", ["finding_id"]
    )
    op.create_index(
        "ix_finding_source_links_source",
        "finding_source_links",
        ["source_type", "source_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_finding_source_links_source", table_name="finding_source_links"
    )
    op.drop_index(
        "ix_finding_source_links_finding_id", table_name="finding_source_links"
    )
    op.drop_table("finding_source_links")
    op.drop_index(
        "ix_finding_evidence_links_finding_id", table_name="finding_evidence_links"
    )
    op.drop_table("finding_evidence_links")
    for index_name in (
        "ix_findings_source_run_id",
        "ix_findings_case_kind",
        "ix_findings_case_status",
        "ix_findings_case_id",
    ):
        op.drop_index(index_name, table_name="findings")
    op.drop_table("findings")
