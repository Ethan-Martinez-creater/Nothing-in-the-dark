"""Second-round migration tests (FC2-MIG-01~03): 0053 normalization.

SQLite 分支验证（engine 无法跑 0044→0053 全链：早期 migration 是
PostgreSQL-only，含 ``CREATE EXTENSION vector``）：
直接以 Alembic MigrationContext 执行 0053 的 upgrade()/downgrade() 函数体，
验证可执行性、旧 TEXT 数据保留、与 snapshot_of 运行时兼容。

PostgreSQL 的 TEXT → JSON 真实迁移在交付环节用生产库 alembic 实测
（见 delivery 文档 Second-Round Debate Fix Summary → FC2-02）。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from app.application.debate_service import snapshot_of

MODULE_NAME = (
    "migrations.versions."
    "20260907_0053_normalize_finding_debate_schema"
)
M53 = importlib.import_module(MODULE_NAME)


def _build_debates_table(engine, *, context_snapshot_type: str = "TEXT") -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE debates ("
            " id VARCHAR(36) PRIMARY KEY,"
            " mode VARCHAR(32) NOT NULL DEFAULT 'case_debate',"
            f" context_snapshot {context_snapshot_type} NOT NULL DEFAULT '{{}}')"
        )


def _run_upgrade(engine) -> None:
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        operations = Operations(context)
        previous_op = getattr(M53, "op", None)
        M53.op = operations
        try:
            M53.upgrade()
        finally:
            if previous_op is None:
                delattr(M53, "op")
            else:
                M53.op = previous_op


def _run_downgrade(engine) -> None:
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        operations = Operations(context)
        previous_op = getattr(M53, "op", None)
        M53.op = operations
        try:
            M53.downgrade()
        finally:
            if previous_op is None:
                delattr(M53, "op")
            else:
                M53.op = previous_op


async def test_fc2_mig_01_fresh_upgrade_is_valid(tmp_path: Path) -> None:
    """0053 revision 链正确；fresh 0052-形态库上执行 upgrade 不破坏 schema。"""
    assert M53.revision == "20260907_0053"
    assert M53.down_revision == "20260906_0052"

    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    _build_debates_table(engine, context_snapshot_type="TEXT")
    _run_upgrade(engine)

    with engine.connect() as conn:
        cols = conn.execute(
            text("PRAGMA table_info(debates)")
        ).fetchall()
        names = {row[1] for row in cols}
        assert {"id", "mode", "context_snapshot"}.issubset(names)
    engine.dispose()


async def test_fc2_mig_02_legacy_text_data_preserved(tmp_path: Path) -> None:
    """旧 TEXT snapshot 历史数据升级后保留，且 snapshot_of 读回 dict。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    _build_debates_table(engine, context_snapshot_type="TEXT")
    legacy_value = '{"finding":{"id":"f1","statement":"test"}}'
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO debates (id, context_snapshot) VALUES ('d1', ?)",
            (legacy_value,),
        )

    _run_upgrade(engine)

    with engine.connect() as conn:
        stored = conn.execute(
            text("SELECT context_snapshot FROM debates WHERE id = 'd1'")
        ).scalar()
    assert stored is not None
    # 兼容层读回 dict（无论底层是 str 还是已解析）
    parsed = snapshot_of(SimpleNamespace(context_snapshot=stored))
    assert parsed["finding"]["id"] == "f1"
    assert parsed["finding"]["statement"] == "test"
    engine.dispose()


async def test_fc2_mig_03_downgrade_executes(tmp_path: Path) -> None:
    """0053 → 0052 downgrade 可执行，不破坏 Alembic 状态。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'downgrade.db'}")
    _build_debates_table(engine, context_snapshot_type="TEXT")
    _run_upgrade(engine)
    _run_downgrade(engine)

    with engine.connect() as conn:
        cols = conn.execute(
            text("PRAGMA table_info(debates)")
        ).fetchall()
        names = {row[1] for row in cols}
        assert {"id", "mode", "context_snapshot"}.issubset(names)
    engine.dispose()


async def test_fc2_mig_04_snapshot_of_tolerates_bad_text(tmp_path: Path) -> None:
    """非法 TEXT 快照值 fail-safe 为 {}（运行时兼容层）。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'bad.db'}")
    _build_debates_table(engine, context_snapshot_type="TEXT")
    _run_upgrade(engine)

    parsed = snapshot_of(SimpleNamespace(context_snapshot="not-json"))
    assert parsed == {}
    parsed_empty = snapshot_of(SimpleNamespace(context_snapshot=""))
    assert parsed_empty == {}
    engine.dispose()