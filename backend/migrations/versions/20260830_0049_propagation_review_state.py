"""FC1: propagation edge human review tri-state.

Adds ``propagation_edges.human_review_state`` (unreviewed/confirmed/rejected)
while keeping ``human_confirmed`` as a compatibility column.

Backfill rules (conservative by design):
- ``human_confirmed`` true rows -> ``confirmed``;
- false rows -> ``rejected`` **only** when the ``evaluations`` audit proves the
  latest manual propagation decision for that edge was a rejection
  (metric ``propagation_edge_human_confirmation``, score < 1.0);
- anything the audit cannot prove -> ``unreviewed`` (never guessed).

The backfill is row-wise Python (same convention as other dialect-aware
migrations in this repository, e.g. 0009): PostgreSQL returns JSON details
as dicts, SQLite as TEXT -- both are handled.

Revision ID: 20260830_0049
Revises: 20260829_0048
"""

import json

import sqlalchemy as sa
from alembic import context as _alembic_context
from alembic import op

revision = "20260830_0049"
down_revision = "20260829_0048"
branch_labels = None
depends_on = None

CONFIRMATION_METRIC = "propagation_edge_human_confirmation"


def _json_details(raw: object) -> dict[str, object]:
    """evaluations.details is JSON: TEXT on SQLite, dict on PostgreSQL."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _is_offline_mode() -> bool:
    """True under alembic --sql runs (no live connection available).

    Direct Operations-based execution (tests) has no EnvironmentContext and
    stays on the online path.
    """
    try:
        return bool(_alembic_context.is_offline_mode())
    except Exception:  # no active EnvironmentContext
        return False


def _backfill_rows(conn: sa.Connection) -> None:
    """Row-wise conservative backfill (SQLite TEXT / PostgreSQL dict JSON)."""
    latest: dict[str, bool] = {}
    rows = conn.execute(
        sa.text(
            "SELECT details, score FROM evaluations "
            f"WHERE metric = '{CONFIRMATION_METRIC}' ORDER BY created_at"
        )
    ).fetchall()
    for details_raw, score in rows:
        edge_id = _json_details(details_raw).get("edge_id")
        if isinstance(edge_id, str) and edge_id and score is not None:
            latest[edge_id] = float(score) >= 1.0

    edges = conn.execute(
        sa.text("SELECT id, human_confirmed FROM propagation_edges")
    ).fetchall()
    for edge_id, human_confirmed in edges:
        if human_confirmed:
            state = "confirmed"
        elif latest.get(edge_id) is False:
            # Audit proves the latest manual decision was a rejection.
            state = "rejected"
        else:
            # Nothing reliable proves a human rejection -> never guess.
            state = "unreviewed"
        conn.execute(
            sa.text(
                "UPDATE propagation_edges SET human_review_state = :state "
                "WHERE id = :edge_id"
            ),
            {"state": state, "edge_id": edge_id},
        )


def upgrade() -> None:
    op.add_column(
        "propagation_edges",
        sa.Column(
            "human_review_state",
            sa.String(length=16),
            nullable=True,
            server_default="unreviewed",
        ),
    )
    op.create_index(
        "ix_propagation_edges_human_review_state",
        "propagation_edges",
        ["human_review_state"],
    )

    # Online runs backfill conservatively; offline (--sql) runs have no
    # connection and emit the DDL only.
    if not _is_offline_mode():
        _backfill_rows(op.get_bind())

    # SQLite cannot ALTER COLUMN SET NOT NULL; batch mode recreates the table.
    with op.batch_alter_table("propagation_edges") as batch_op:
        batch_op.alter_column(
            "human_review_state",
            existing_type=sa.String(length=16),
            nullable=False,
        )


def downgrade() -> None:
    op.drop_index(
        "ix_propagation_edges_human_review_state", table_name="propagation_edges"
    )
    with op.batch_alter_table("propagation_edges") as batch_op:
        batch_op.drop_column("human_review_state")
    # The legacy human_confirmed column is intentionally kept.
