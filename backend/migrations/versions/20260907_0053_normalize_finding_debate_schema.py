"""Normalize finding debate schema: unify debates.context_snapshot to JSON.

Revision ID: 20260907_0053
Revises: 20260906_0052

背景（FC2-02）：0052 曾经以 ``sa.Text()`` 发布，随后被原地修改为
``sa.JSON()``，导致不同数据库出现 TEXT / JSON 两种形态的
``debates.context_snapshot``。0052 视为已发布、不再修改；0053 负责
把旧 TEXT 库安全统一到 JSON，新库（已 JSON）幂等无操作。

- PostgreSQL：先修复非法/NULL/空串历史值（fail-safe 为 '{}'），
  再 ``ALTER COLUMN TYPE JSON USING context_snapshot::json``；
- SQLite：``sa.JSON`` 底层即 TEXT，无需迁移数据或类型。

``snapshot_of()`` 运行时容错层继续保留（兼容未升级的外部副本）。
"""

import sqlalchemy as sa
from alembic import op

revision = "20260907_0053"
down_revision = "20260906_0052"
branch_labels = None
depends_on = None


def _is_legacy_text_context_snapshot(bind) -> bool:
    """PG 上检测 context_snapshot 列是否为旧 TEXT/CHAR 形态。"""
    result = bind.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'debates' AND column_name = 'context_snapshot'"
        )
    ).scalar()
    if result is None:
        # 列不存在（异常库）：让后续 ALTER 自然失败并暴露问题
        return True
    return result in ("text", "character varying")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite：JSON 底层即 TEXT，类型与数据均无需迁移；batch rebuild
        # 反而有丢失 server_default / 数据被截断的风险。
        return
    if not _is_legacy_text_context_snapshot(bind):
        # 已是 JSON/JSONB（新 0052 库）：幂等无操作。
        return
    # 1. 修复历史坏值：NULL / 空串 / 空白 / 非 JSON 形态 → '{}'（fail-safe，
    #    不让单条历史坏值整体搞挂迁移）。
    bind.execute(
        sa.text(
            "UPDATE debates SET context_snapshot = '{}' "
            "WHERE context_snapshot IS NULL "
            "OR btrim(context_snapshot) = '' "
            "OR left(btrim(context_snapshot), 1) <> '{'"
        )
    )
    # 2. TEXT → JSON，保留已有 snapshot 内容（前置修复后全部可安全 ::json）。
    with op.batch_alter_table("debates") as batch_op:
        batch_op.alter_column(
            "context_snapshot",
            type_=sa.JSON(),
            postgresql_using="context_snapshot::json",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.batch_alter_table("debates") as batch_op:
        batch_op.alter_column(
            "context_snapshot",
            type_=sa.Text(),
            postgresql_using="context_snapshot::text",
        )