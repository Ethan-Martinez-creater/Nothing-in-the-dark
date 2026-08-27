"""Expert agents: artifact run linkage and agent mailbox.

Revision ID: 20260806_0009
Revises: 20260806_0008
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0009"
down_revision: str | None = "20260806_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # artifacts: task_id becomes optional; artifacts may instead belong to
    # an agent run (expert output saved by the Graph Worker).
    op.add_column(
        "artifacts",
        sa.Column("run_id", sa.String(36), nullable=True),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_foreign_key(
        "fk_artifacts_run_id_agent_runs",
        "artifacts",
        "agent_runs",
        ["run_id"],
        ["id"],
    )
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("artifacts") as batch_op:
            batch_op.alter_column("task_id", existing_type=sa.String(36), nullable=True)
    else:
        op.alter_column(
            "artifacts",
            "task_id",
            existing_type=sa.String(36),
            nullable=True,
        )

    # agent_messages: typed mailbox between parent and child agent runs.
    op.create_table(
        "agent_messages",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("sender_run_id", sa.String(36), nullable=False),
        sa.Column("receiver_run_id", sa.String(36), nullable=False),
        sa.Column("message_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["sender_run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["receiver_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_messages_sender_run_id",
        "agent_messages",
        ["sender_run_id"],
    )
    op.create_index(
        "ix_agent_messages_receiver_run_id",
        "agent_messages",
        ["receiver_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_messages_receiver_run_id",
        table_name="agent_messages",
    )
    op.drop_index(
        "ix_agent_messages_sender_run_id",
        table_name="agent_messages",
    )
    op.drop_table("agent_messages")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("artifacts") as batch_op:
            batch_op.alter_column("task_id", existing_type=sa.String(36), nullable=False)
    else:
        op.alter_column(
            "artifacts",
            "task_id",
            existing_type=sa.String(36),
            nullable=False,
        )
    op.drop_constraint("fk_artifacts_run_id_agent_runs", "artifacts", type_="foreignkey")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_column("artifacts", "run_id")
