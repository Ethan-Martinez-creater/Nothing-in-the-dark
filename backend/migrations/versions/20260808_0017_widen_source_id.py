"""Widen source_id columns to 500 chars.

2026-08-08 手动冒烟发现：模型调用 write_memory 时把 URL/长 ID 当作
source_id 传入，100 字符限制导致 pydantic ValidationError，进而整个
run 失败（agent_run_failed）。对齐
`app/infrastructure/database/models.py` 中 memories / evidence 两处
`String(100)` → `String(500)` 的定义。

Revision ID: 20260808_0017
Revises: 20260807_0016
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0017"
down_revision: str | None = "20260807_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("memories", "source_id", type_=sa.String(500))
    op.alter_column("evidence", "source_id", type_=sa.String(500))


def downgrade() -> None:
    op.alter_column("evidence", "source_id", type_=sa.String(100))
    op.alter_column("memories", "source_id", type_=sa.String(100))
