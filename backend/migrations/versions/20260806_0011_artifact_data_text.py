"""Store artifacts.data as TEXT so Chinese keyword search works.

PostgreSQL's ``json`` type re-escapes non-ASCII characters in its output
(``data::text`` and the tsvector generated column built from it), so the
artifact branch of hybrid search can never match Chinese content. Convert
the column to TEXT, keeping the real characters via ``data::jsonb #>> '{}'``
(which returns text without json escaping). SQLite already stores TEXT, so
this migration is a no-op there.

Revision ID: 20260806_0011
Revises: 20260806_0010
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260806_0011"
down_revision: str | None = "20260806_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors the artifacts entry in 20260806_0010.
_SEARCH_EXPRESSION = "coalesce(title, '') || ' ' || coalesce(data::text, '')"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite already stores the column as TEXT
    op.drop_index("ix_artifacts_search_vector", table_name="artifacts")
    op.drop_column("artifacts", "search_vector")
    # #>> returns plain text (no json escaping), so existing rows keep
    # their real characters instead of \uXXXX escapes.
    op.execute(
        "ALTER TABLE artifacts ALTER COLUMN data TYPE TEXT "
        "USING (data::jsonb #>> '{}')"
    )
    op.execute(
        "ALTER TABLE artifacts "
        "ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', "
        f"{_SEARCH_EXPRESSION})) STORED"
    )
    op.execute(
        "CREATE INDEX ix_artifacts_search_vector "
        "ON artifacts USING gin (search_vector)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.drop_index("ix_artifacts_search_vector", table_name="artifacts")
    op.drop_column("artifacts", "search_vector")
    # Rows store serialized JSON text, which the json type parses fine.
    op.execute(
        "ALTER TABLE artifacts ALTER COLUMN data TYPE json USING data::json"
    )
    op.execute(
        "ALTER TABLE artifacts "
        "ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', "
        f"{_SEARCH_EXPRESSION})) STORED"
    )
    op.execute(
        "CREATE INDEX ix_artifacts_search_vector "
        "ON artifacts USING gin (search_vector)"
    )
