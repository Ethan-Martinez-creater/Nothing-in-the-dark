"""Persist retry history and cache flag on tool_calls.

M6 Tool System 引入这两列时未补 PG 迁移（sqlite 靠 create_all 生效），
导致真实 PostgreSQL 部署在 tool 幂等查询处报 UndefinedColumnError。
本迁移对齐 `app/infrastructure/database/models.py` 中 ToolCall 定义。

Revision ID: 20260807_0016
Revises: 20260807_0015
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0016"
down_revision: str | None = "20260807_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_calls",
        sa.Column("retry_history", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "tool_calls",
        sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("tool_calls", "cached")
    op.drop_column("tool_calls", "retry_history")
