"""Add idempotency_key to monitor_executions (MON-P0-01).

run-now 使用稳定幂等键：唯一约束 (monitor_id, idempotency_key) 让同一键
重复调用命中同一 execution；调度产生的 execution 该列为 NULL，不受影响。

Revision ID: 20260820_0026
Revises: 20260820_0025
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0026"
down_revision: str | None = "20260820_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "monitor_executions",
        sa.Column("idempotency_key", sa.String(64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_monitor_execution_monitor_key",
        "monitor_executions",
        ["monitor_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_monitor_execution_monitor_key",
        "monitor_executions",
        type_="unique",
    )
    op.drop_column("monitor_executions", "idempotency_key")
