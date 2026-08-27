"""Add trace_id correlation to run events (19).

端到端可观测性：run_events 增加 trace_id 关联（SSE 事件打开对应运行）。

Revision ID: 20260823_0040
Revises: 20260823_0039
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0040"
down_revision: str | None = "20260823_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_events",
        sa.Column("trace_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_run_events_trace_id", "run_events", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_run_events_trace_id", table_name="run_events")
    op.drop_column("run_events", "trace_id")
