"""Add fingerprint to coordination_clusters (INT-P0-03).

协同群体重跑幂等：fingerprint 由 case/窗口/算法版本/排序成员稳定计算，
唯一约束避免同一次运行重复建群。

Revision ID: 20260820_0028
Revises: 20260820_0027
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0028"
down_revision: str | None = "20260820_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "coordination_clusters",
        sa.Column("fingerprint", sa.String(64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_coordination_cluster_fingerprint",
        "coordination_clusters",
        ["case_id", "fingerprint"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_coordination_cluster_fingerprint",
        "coordination_clusters",
        type_="unique",
    )
    op.drop_column("coordination_clusters", "fingerprint")
