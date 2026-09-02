"""Idempotent backfill of propagation_edges.human_review_state.

Root cause: the DB was bootstrapped with ``Base.metadata.create_all``
(``Database.create_schema``), which creates tables but never adds columns to
existing tables. The ``propagation_edges`` table predates migration
``20260830_0049`` which adds ``human_review_state``, so the column is missing
and any query referencing it (e.g. the Network propagation graph) fails with
``UndefinedColumnError``.

Running ``alembic upgrade head`` is NOT safe here because later migrations
(0046..0050) contain ``create_table`` for tables that already exist, so this
script applies just the missing column idempotently instead.

Backfill is conservative (same rules as migration 0049):
- ``human_confirmed`` true rows -> ``confirmed``;
- rows where the evaluations audit proves a manual rejection -> ``rejected``;
- everything else -> ``unreviewed``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.infrastructure.database import Database  # noqa: E402

CONFIRMATION_METRIC = "propagation_edge_human_confirmation"


def _json_details(raw: object) -> dict[str, object]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


async def main() -> int:
    settings = get_settings()
    if not settings.database_url.startswith("postgresql"):
        print("This backfill targets PostgreSQL only.", file=sys.stderr)
        return 2
    db = Database(settings.database_url)
    try:
        async with db.engine.begin() as conn:
            exists = await conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='propagation_edges' "
                    "AND column_name='human_review_state'"
                )
            )
            if exists:
                print("column human_review_state already present; nothing to do")
                return 0

            await conn.execute(
                text(
                    "ALTER TABLE propagation_edges "
                    "ADD COLUMN human_review_state VARCHAR(16) "
                    "NOT NULL DEFAULT 'unreviewed'"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX ix_propagation_edges_human_review_state "
                    "ON propagation_edges (human_review_state)"
                )
            )

            # Conservative backfill.
            latest: dict[str, bool] = {}
            rows = await conn.execute(
                text(
                    "SELECT details, score FROM evaluations "
                    "WHERE metric=:m ORDER BY created_at"
                ),
                {"m": CONFIRMATION_METRIC},
            )
            for details_raw, score in rows:
                edge_id = _json_details(details_raw).get("edge_id")
                if isinstance(edge_id, str) and edge_id and score is not None:
                    latest[edge_id] = float(score) >= 1.0
            edges = await conn.execute(
                text("SELECT id, human_confirmed FROM propagation_edges")
            )
            for edge_id, human_confirmed in edges:
                if human_confirmed:
                    state = "confirmed"
                elif latest.get(edge_id) is False:
                    state = "rejected"
                else:
                    state = "unreviewed"
                await conn.execute(
                    text(
                        "UPDATE propagation_edges SET human_review_state=:s "
                        "WHERE id=:id"
                    ),
                    {"s": state, "id": edge_id},
                )
            print("added propagation_edges.human_review_state + backfilled")
        return 0
    finally:
        await db.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
