"""Migration 0051 tests: V3 intelligence schema upgrade / downgrade / constraints.

The production Alembic chain is PostgreSQL-only in places (0003 pgvector), so
these tests execute the real 0050/0051 ``upgrade()`` / ``downgrade()`` through
Alembic Operations against a SQLite database seeded with minimal ``cases`` and
``source_posts`` tables (SQLite does not enforce FKs, but ``CREATE INDEX``
requires the target table to exist).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION_0050 = (
    BACKEND_DIR / "migrations" / "versions" / "20260901_0050_collection_runs.py"
)
MIGRATION_0051 = (
    BACKEND_DIR / "migrations" / "versions" / "20260903_0051_v3_intelligence.py"
)

V3_TABLES = (
    "investigation_quality",
    "workspace_entities",
    "workspace_entity_keys",
    "workspace_entity_case_links",
    "workspace_entity_relations",
    "cross_investigation_links",
    "derived_signals",
    "derived_signal_case_links",
)

# Creation order in 0051 (children after parents); reversed for teardown.
V3_TABLES_DOWNGRADE_ORDER = tuple(reversed(V3_TABLES))

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "investigation_quality": (
        "case_id",
        "overall_score",
        "grade",
        "input_fingerprint",
        "algorithm_version",
        "computed_at",
    ),
    "workspace_entities": (
        "entity_type",
        "canonical_name",
        "aliases_json",
        "attributes_json",
        "status",
        "created_by",
    ),
    "workspace_entity_keys": (
        "entity_id",
        "key_type",
        "key_value",
        "confidence",
        "method",
    ),
    "workspace_entity_case_links": (
        "entity_id",
        "case_id",
        "source_type",
        "source_id",
        "metadata_json",
    ),
    "workspace_entity_relations": (
        "left_entity_id",
        "right_entity_id",
        "relation_type",
        "status",
        "source_case_id",
    ),
    "cross_investigation_links": (
        "left_case_id",
        "right_case_id",
        "relation_type",
        "is_active",
        "fingerprint",
    ),
    "derived_signals": (
        "case_id",
        "signal_type",
        "status",
        "detector_active",
        "fingerprint",
        "occurrence_count",
    ),
    "derived_signal_case_links": ("signal_id", "case_id", "created_at"),
}

REQUIRED_UNIQUES: dict[str, set[frozenset[str]]] = {
    "workspace_entity_keys": {frozenset({"key_type", "key_value"})},
    "workspace_entity_case_links": {frozenset({"case_id", "source_type", "source_id"})},
    "cross_investigation_links": {frozenset({"fingerprint"})},
    "derived_signals": {frozenset({"fingerprint"})},
    "derived_signal_case_links": {frozenset({"signal_id", "case_id"})},
}


def _load_migration(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0050():
    return _load_migration("migration_0050", MIGRATION_0050)


def _load_0051():
    return _load_migration("migration_0051", MIGRATION_0051)


def _seed_base_tables(engine: sa.Engine) -> None:
    """Create minimal cases/source_posts targets for FKs and the new index."""
    metadata = sa.MetaData()
    sa.Table("cases", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "source_posts",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
    )
    metadata.create_all(engine)


def _run_ops(engine: sa.Engine, fn) -> None:
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            fn()
        conn.commit()


def _upgrade_all(engine: sa.Engine) -> None:
    _seed_base_tables(engine)
    _run_ops(engine, _load_0050().upgrade)
    _run_ops(engine, _load_0051().upgrade)


def _upgrade_0051(engine: sa.Engine) -> None:
    _seed_base_tables(engine)
    _run_ops(engine, _load_0051().upgrade)


def _downgrade_0051(engine: sa.Engine) -> None:
    _run_ops(engine, _load_0051().downgrade)


def test_migration_0051_upgrade_creates_v3_schema(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    try:
        _upgrade_all(engine)
        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        for table in (*V3_TABLES, "collection_runs", "source_posts"):
            assert table in tables, f"missing table {table}"

        for table, required in REQUIRED_COLUMNS.items():
            columns = {column["name"] for column in inspector.get_columns(table)}
            missing = set(required) - columns
            assert not missing, f"{table} missing columns {sorted(missing)}"

        source_post_indexes = {
            index["name"]: index for index in inspector.get_indexes("source_posts")
        }
        composite = source_post_indexes.get("ix_source_posts_content_hash_case")
        assert composite is not None, "source_posts composite index missing"
        assert composite["column_names"] == ["content_hash", "case_id"]
        assert not composite["unique"]
    finally:
        engine.dispose()


def test_migration_0051_upgrade_downgrade_upgrade_round_trip(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    try:
        _upgrade_all(engine)

        _downgrade_0051(engine)
        tables = set(sa.inspect(engine).get_table_names())
        for table in V3_TABLES:
            assert table not in tables, f"table {table} should be gone after downgrade"
        source_post_indexes = {
            index["name"] for index in sa.inspect(engine).get_indexes("source_posts")
        }
        assert "ix_source_posts_content_hash_case" not in source_post_indexes
        # 0051 downgrade must not touch the 0050 table.
        assert "collection_runs" in tables

        _upgrade_0051(engine)
        tables = set(sa.inspect(engine).get_table_names())
        for table in V3_TABLES:
            assert table in tables, f"table {table} should be back after re-upgrade"
        source_post_indexes = {
            index["name"] for index in sa.inspect(engine).get_indexes("source_posts")
        }
        assert "ix_source_posts_content_hash_case" in source_post_indexes
    finally:
        engine.dispose()


def test_migration_0051_unique_constraints(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    try:
        _upgrade_all(engine)
        inspector = sa.inspect(engine)
        for table, expected in REQUIRED_UNIQUES.items():
            actual = {
                frozenset(constraint["column_names"])
                for constraint in inspector.get_unique_constraints(table)
                if constraint.get("column_names")
            }
            missing = expected - actual
            assert not missing, f"{table} missing unique constraints {sorted(missing)}"
    finally:
        engine.dispose()


def test_migration_0051_postgres_upgrade() -> None:
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("PostgreSQL test URL not configured")

    engine = sa.create_engine(url)
    try:
        # Reset leftovers so the test is re-runnable against a persistent DB.
        with engine.begin() as conn:
            for table in V3_TABLES_DOWNGRADE_ORDER:
                conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}" CASCADE')
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_source_posts_content_hash_case")
            conn.exec_driver_sql("DROP TABLE IF EXISTS source_posts CASCADE")
            conn.exec_driver_sql("DROP TABLE IF EXISTS cases CASCADE")

        _seed_base_tables(engine)
        _run_ops(engine, _load_0051().upgrade)

        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        for table in V3_TABLES:
            assert table in tables, f"missing table {table}"
        for table, required in REQUIRED_COLUMNS.items():
            columns = {column["name"] for column in inspector.get_columns(table)}
            missing = set(required) - columns
            assert not missing, f"{table} missing columns {sorted(missing)}"

        source_post_indexes = {
            index["name"]: index for index in inspector.get_indexes("source_posts")
        }
        composite = source_post_indexes.get("ix_source_posts_content_hash_case")
        assert composite is not None, "source_posts composite index missing"
        assert composite["column_names"] == ["content_hash", "case_id"]

        for table, expected in REQUIRED_UNIQUES.items():
            actual = {
                frozenset(constraint["column_names"])
                for constraint in inspector.get_unique_constraints(table)
                if constraint.get("column_names")
            }
            missing = expected - actual
            assert not missing, f"{table} missing unique constraints {sorted(missing)}"

        _run_ops(engine, _load_0051().downgrade)
        tables = set(sa.inspect(engine).get_table_names())
        for table in V3_TABLES:
            assert table not in tables, f"table {table} should be gone after downgrade"
    finally:
        engine.dispose()
