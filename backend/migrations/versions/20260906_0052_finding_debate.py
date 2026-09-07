"""Finding-level adversarial challenge: debates.mode / finding_id / context_snapshot.

Revision ID: 20260906_0052
Revises: 20260903_0051

计划文档 §4/M1：Debate 与 Finding 建立稳定持久化关系。
- mode: legacy 全案辩论默认兼容为 case_debate；
- finding_id: nullable FK -> findings.id，加索引；
- context_snapshot: 创建时固化的 Finding 上下文快照（JSON stored as TEXT，
  沿用 0050/0051 约定）。
不删除旧列，不影响 DebateMessage / DebateVote。
"""

import sqlalchemy as sa
from alembic import op

revision = "20260906_0052"
down_revision = "20260903_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("debates") as batch_op:
        batch_op.add_column(
            sa.Column(
                "mode",
                sa.String(length=32),
                nullable=False,
                server_default="case_debate",
            )
        )
        batch_op.add_column(sa.Column("finding_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column(
                "context_snapshot",
                sa.JSON(),
                nullable=False,
                server_default="{}",
            )
        )
        batch_op.create_index("ix_debates_finding_id", ["finding_id"])
        batch_op.create_foreign_key(
            "fk_debates_finding_id_findings",
            "findings",
            ["finding_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("debates") as batch_op:
        batch_op.drop_constraint("fk_debates_finding_id_findings", type_="foreignkey")
        batch_op.drop_index("ix_debates_finding_id")
        batch_op.drop_column("context_snapshot")
        batch_op.drop_column("finding_id")
        batch_op.drop_column("mode")
