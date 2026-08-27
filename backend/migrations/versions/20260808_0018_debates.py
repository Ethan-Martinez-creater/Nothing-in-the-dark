"""Create debate tables (multi-role verification debates).

辩论验证功能：以各平台采集数据为背景知识的多角色辩论
（四轮：陈述→反驳→投票→主持人总结，用户可插话）。

Revision ID: 20260808_0018
Revises: 20260808_0017
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0018"
down_revision: str | None = "20260808_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "debates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "case_id", sa.String(36), sa.ForeignKey("cases.id"), index=True
        ),
        sa.Column("title", sa.String(200), nullable=False, server_default="多平台观点辩论"),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="in_progress",
            index=True,
        ),
        sa.Column("round", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("platform_roles", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "debate_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "debate_id", sa.String(36), sa.ForeignKey("debates.id"), index=True
        ),
        sa.Column("role", sa.String(32), nullable=False, index=True),
        sa.Column("platform", sa.String(32), nullable=True),
        sa.Column("round", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "debate_votes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "debate_id", sa.String(36), sa.ForeignKey("debates.id"), index=True
        ),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("choice", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("debate_votes")
    op.drop_table("debate_messages")
    op.drop_table("debates")
