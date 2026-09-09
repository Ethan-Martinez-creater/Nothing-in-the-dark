"""Platform auth credential storage (Phase 2).

Revision ID: 20260909_0054
Revises: 20260907_0053
Create Date: 2026-09-09

AES-256-GCM 加密的平台登录凭据表。cookies 载荷只以 nonce + ciphertext
落库；SQLite 与 PostgreSQL 通用（仅标准列类型）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0054"
down_revision: str | None = "20260907_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "platform_auth_credentials"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("account_label", sa.String(200), nullable=True),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="active"
        ),
        sa.Column(
            "state_format_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("nonce_b64", sa.Text(), nullable=False),
        sa.Column("ciphertext_b64", sa.Text(), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_platform_auth_credentials_platform",
        _TABLE,
        ["platform"],
        unique=True,
    )
    op.create_index(
        "ix_platform_auth_credentials_status",
        _TABLE,
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_platform_auth_credentials_status", table_name=_TABLE)
    op.drop_index("ix_platform_auth_credentials_platform", table_name=_TABLE)
    op.drop_table(_TABLE)
