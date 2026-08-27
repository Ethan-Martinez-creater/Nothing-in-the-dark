"""Enforce one-time approval consumption (21/22).

一次性授权消费：execution_authorizations 增加 action_family /
resource_id，approval_id 唯一（一个审批至多产生一次执行授权，杜绝
同一审批被重复用于多个 Kill Switch / 死信重试 / 工具调用）。

Revision ID: 20260823_0044
Revises: 20260823_0043
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0044"
down_revision: str | None = "20260823_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "execution_authorizations",
        sa.Column("action_family", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "execution_authorizations",
        sa.Column("resource_id", sa.String(160), nullable=False, server_default=""),
    )
    # 一个审批至多一条执行授权：重复 issue 直接撞唯一约束。
    op.create_unique_constraint(
        "uq_execution_authorization_approval",
        "execution_authorizations",
        ["approval_id"],
    )
    op.create_index(
        "ix_execution_authorizations_consumed",
        "execution_authorizations",
        ["approval_id", "consumed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_execution_authorizations_consumed", table_name="execution_authorizations")
    op.drop_constraint(
        "uq_execution_authorization_approval",
        "execution_authorizations",
        type_="unique",
    )
    op.drop_column("execution_authorizations", "resource_id")
    op.drop_column("execution_authorizations", "action_family")
