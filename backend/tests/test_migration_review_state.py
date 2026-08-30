"""FC1: migration 0048 -> 0049 backfill correctness for propagation tri-state.

The production Alembic chain is PostgreSQL-only in places (e.g. 0003 runs
``CREATE EXTENSION vector``), so full-chain runs belong to the PG migration
verifier script. This module instead validates the 0049 revision logic itself:

- build the 0048-era table shape via create_schema + drop of the new column;
- execute the real ``upgrade()`` / ``downgrade()`` through Alembic Operations;
- assert the conservative backfill contract:
  * human_confirmed true -> confirmed;
  * false + latest audit decision is a rejection -> rejected;
  * false without provable audit (or inconsistent data) -> unreviewed.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime as _DATETIME
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.infrastructure.database import Database

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION_0049 = (
    BACKEND_DIR / "migrations" / "versions" / "20260830_0049_propagation_review_state.py"
)


def _load_0049():
    spec = importlib.util.spec_from_file_location("migration_0049", MIGRATION_0049)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _build_0048_shape(db_path: Path) -> None:
    """Create the current schema, then strip the 0049 column and index."""
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    await database.dispose()
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "DROP INDEX IF EXISTS ix_propagation_edges_human_review_state"
            )
            conn.exec_driver_sql(
                "ALTER TABLE propagation_edges DROP COLUMN human_review_state"
            )
            conn.commit()
    finally:
        engine.dispose()


def _insert_minimal(
    conn: sa.Connection, metadata: sa.MetaData, table_name: str, values: dict[str, Any]
) -> None:
    """Insert one row, filling NOT NULL columns without server defaults.

    Keep the fixture focused on what the migration logic reads; every other
    mandatory column gets a neutral value so the row stays schema-valid.
    """
    table = sa.Table(table_name, metadata, autoload_with=conn)
    filled = dict(values)
    for column in table.columns:
        if column.name in filled or column.nullable or column.server_default is not None:
            continue
        try:
            python_type = column.type.python_type
        except NotImplementedError:
            python_type = str
        if python_type is bool:
            filled[column.name] = 0
        elif python_type in (int, float):
            filled[column.name] = 0
        elif python_type is dict:
            filled[column.name] = {}
        elif python_type is list:
            filled[column.name] = []
        elif python_type.__name__ == "datetime":
            filled[column.name] = _DATETIME(2026, 8, 1)
        else:
            filled[column.name] = ""
    conn.execute(table.insert().values(**filled))


def _seed_0048_world(db_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            metadata = sa.MetaData()
            _insert_minimal(
                conn, metadata, "cases",
                {"id": "case-mig", "title": "迁移回填", "topic": "迁移回填"},
            )
            post_ids = ["p1", "p2", "p3", "p4", "p5", "p6"]
            for post_id in post_ids:
                _insert_minimal(
                    conn, metadata, "source_posts",
                    {"id": post_id, "case_id": "case-mig", "platform": "weibo",
                     "native_id": post_id},
                )
            # edge-a..f cover every backfill branch asserted below (distinct
            # post pairs satisfy the case/source/target unique constraint).
            pairs = list(zip(post_ids, post_ids[1:] + post_ids[:1]))
            for (source, target), (edge_id, confirmed) in zip(
                pairs,
                (("edge-a", 1), ("edge-b", 0), ("edge-c", 0),
                 ("edge-d", 0), ("edge-e", 0), ("edge-f", 0)),
            ):
                _insert_minimal(
                    conn, metadata, "propagation_edges",
                    {"id": edge_id, "case_id": "case-mig", "source_post_id": source,
                     "target_post_id": target, "relation": "observed",
                     "confidence": 0.8, "algorithm_version": "1.0.0",
                     "human_confirmed": confirmed},
                )
            # (score, edge_id) audit rows; created_at asc == insertion order.
            audits = [
                (0.0, "edge-b"),  # latest rejection -> rejected backfill
                (1.0, "edge-c"),  # latest confirm but row false -> unreviewed
                (0.0, "edge-e"),
                (1.0, "edge-e"),  # latest confirm -> unreviewed
                (1.0, "edge-f"),
                (0.0, "edge-f"),  # latest reject -> rejected backfill
            ]
            for i, (score, edge_id) in enumerate(audits):
                _insert_minimal(
                    conn, metadata, "evaluations",
                    {"id": f"eval-{i}", "case_id": "case-mig",
                     "metric": "propagation_edge_human_confirmation",
                     "score": score,
                     "details": {"edge_id": edge_id, "note": ""}},
                )
    finally:
        engine.dispose()


def _upgrade(db_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                _load_0049().upgrade()
            conn.commit()
    finally:
        engine.dispose()


def _downgrade(db_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                _load_0049().downgrade()
            conn.commit()
    finally:
        engine.dispose()


async def test_0049_backfill_and_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "migration0049.db"
    await _build_0048_shape(db_path)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            cols = {
                row[1]
                for row in conn.exec_driver_sql("PRAGMA table_info(propagation_edges)")
            }
        assert "human_review_state" not in cols
    finally:
        engine.dispose()

    _seed_0048_world(db_path)
    _upgrade(db_path)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            info = conn.exec_driver_sql(
                "PRAGMA table_info(propagation_edges)"
            ).fetchall()
            states = dict(
                conn.exec_driver_sql(
                    "SELECT id, human_review_state FROM propagation_edges"
                ).fetchall()
            )
            indexes = {
                row[1]
                for row in conn.exec_driver_sql(
                    "PRAGMA index_list(propagation_edges)"
                )
            }
        notnull = {row[1]: row[3] for row in info}
        # Final column must be NOT NULL and indexed; legacy column is kept.
        assert notnull["human_review_state"] == 1
        assert "ix_propagation_edges_human_review_state" in indexes
        assert "human_confirmed" in notnull

        assert states["edge-a"] == "confirmed"  # human_confirmed true
        assert states["edge-b"] == "rejected"  # latest audit rejection provable
        assert states["edge-c"] == "unreviewed"  # latest confirm, row false
        assert states["edge-d"] == "unreviewed"  # no audit -> never guessed
        assert states["edge-e"] == "unreviewed"  # audit [reject, confirm]
        assert states["edge-f"] == "rejected"  # audit [confirm, reject]

        # Fresh inserts (no ORM involved) default to unreviewed.
        with engine.begin() as conn:
            _insert_minimal(
                conn, sa.MetaData(), "propagation_edges",
                {"id": "edge-new", "case_id": "case-mig", "source_post_id": "p3",
                 "target_post_id": "p1", "relation": "observed",
                 "confidence": 0.5, "algorithm_version": "1.0.0",
                 "human_confirmed": 0},
            )
        with engine.connect() as conn:
            new_state = conn.exec_driver_sql(
                "SELECT human_review_state FROM propagation_edges "
                "WHERE id = 'edge-new'"
            ).scalar()
        assert new_state == "unreviewed"
    finally:
        engine.dispose()

    # Downgrade: tri-state column dropped, rows and the legacy column survive.
    _downgrade(db_path)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            cols = {
                row[1]
                for row in conn.exec_driver_sql("PRAGMA table_info(propagation_edges)")
            }
            remaining = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM propagation_edges"
            ).scalar()
            confirmed_kept = conn.exec_driver_sql(
                "SELECT human_confirmed FROM propagation_edges WHERE id = 'edge-a'"
            ).scalar()
        assert "human_review_state" not in cols
        assert "human_confirmed" in cols
        assert remaining == 7  # six seeds + the post-upgrade insert
        assert bool(confirmed_kept) is True
    finally:
        engine.dispose()


async def test_0049_upgrade_and_downgrade_on_empty_0048_shape(tmp_path: Path) -> None:
    """Empty 0048-shape database: upgrade adds the column, downgrade removes
    it again without touching other tables (no rows to backfill)."""
    db_path = tmp_path / "fresh0049.db"
    await _build_0048_shape(db_path)
    _upgrade(db_path)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            info = conn.exec_driver_sql(
                "PRAGMA table_info(propagation_edges)"
            ).fetchall()
            state = conn.exec_driver_sql(
                "SELECT human_review_state FROM propagation_edges LIMIT 1"
            ).scalar()
        assert any(row[1] == "human_review_state" for row in info)
        assert state is None  # empty table, no rows to backfill
    finally:
        engine.dispose()

    _downgrade(db_path)
