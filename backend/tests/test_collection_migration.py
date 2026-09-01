"""Migration 0050 tests: collection_runs upgrade / downgrade / upgrade.

Production Alembic chain is PostgreSQL-only in places (e.g. 0003 runs
``CREATE EXTENSION vector``), so full-chain runs belong to the PG migration
verifier. This module executes the real 0050 ``upgrade()`` / ``downgrade()``
through Alembic Operations against an empty SQLite database.
"""

from __future__ import annotations

import importlib.util
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION_0050 = (
    BACKEND_DIR
    / "migrations"
    / "versions"
    / "20260901_0050_collection_runs.py"
)


def _load_0050():
    spec = importlib.util.spec_from_file_location("migration_0050", MIGRATION_0050)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(direction: str, db_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                if direction == "upgrade":
                    _load_0050().upgrade()
                else:
                    _load_0050().downgrade()
            conn.commit()
    finally:
        engine.dispose()


def _tables(db_path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            inspector = sa.inspect(conn)
            return set(inspector.get_table_names())
    finally:
        engine.dispose()


def test_migration_0050_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "mig.db"

    _run("upgrade", db_path)
    tables = _tables(db_path)
    assert "collection_runs" in tables
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            columns = {
                column["name"]
                for column in sa.inspect(conn).get_columns("collection_runs")
            }
    finally:
        engine.dispose()
    for required in (
        "id",
        "case_id",
        "phase",
        "status",
        "request_fingerprint",
        "idempotency_key",
        "request_json",
        "progress_json",
        "posts_collected",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "cancel_requested_at",
        "started_at",
        "completed_at",
    ):
        assert required in columns, f"missing column {required}"

    _run("downgrade", db_path)
    assert "collection_runs" not in _tables(db_path)

    _run("upgrade", db_path)
    assert "collection_runs" in _tables(db_path)
