"""Add ON DELETE policies for round-01 module foreign keys (A-01).

为 01/04/06/07/08 新表的外键统一删除策略：case 域外键与父子外键加
ON DELETE CASCADE；可空审计引用（run_id/post_id/claim_id）用 SET NULL。
应用层 delete_case/delete_monitor 已按顺序显式删除（SQLite 兼容），本迁移
在 PostgreSQL 上补充数据库级联防御。

SQLite 不强制外键且已由应用层删除覆盖，故本迁移在 SQLite 下为 no-op。

Revision ID: 20260820_0025
Revises: 20260820_0024
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260820_0025"
down_revision: str | None = "20260820_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, referenced_table, on_delete)
_CASCADE = [
    ("monitor_definitions", "case_id", "cases", "CASCADE"),
    ("monitor_cursors", "monitor_id", "monitor_definitions", "CASCADE"),
    ("monitor_executions", "monitor_id", "monitor_definitions", "CASCADE"),
    ("alert_rules", "monitor_id", "monitor_definitions", "CASCADE"),
    ("alert_occurrences", "monitor_id", "monitor_definitions", "CASCADE"),
    ("alert_occurrences", "rule_id", "alert_rules", "CASCADE"),
    ("media_derivatives", "asset_id", "media_assets", "CASCADE"),
    ("media_transcripts", "asset_id", "media_assets", "CASCADE"),
    ("media_pipeline_jobs", "asset_id", "media_assets", "CASCADE"),
    ("canonical_entities", "case_id", "cases", "CASCADE"),
    ("entity_mentions", "case_id", "cases", "CASCADE"),
    ("entity_mentions", "entity_id", "canonical_entities", "CASCADE"),
    ("alignment_candidates", "case_id", "cases", "CASCADE"),
    ("content_families", "case_id", "cases", "CASCADE"),
    ("content_family_members", "family_id", "content_families", "CASCADE"),
    ("narrative_memberships", "case_id", "cases", "CASCADE"),
    ("behavior_feature_snapshots", "case_id", "cases", "CASCADE"),
    ("risk_assessments", "case_id", "cases", "CASCADE"),
    ("coordination_clusters", "case_id", "cases", "CASCADE"),
    ("coordination_members", "cluster_id", "coordination_clusters", "CASCADE"),
    ("quality_assessments", "case_id", "cases", "CASCADE"),
    ("analysis_assumptions", "case_id", "cases", "CASCADE"),
    ("sensitivity_runs", "case_id", "cases", "CASCADE"),
    ("alternative_hypotheses", "case_id", "cases", "CASCADE"),
    ("conclusion_confidence", "case_id", "cases", "CASCADE"),
]

_SET_NULL = [
    ("monitor_executions", "run_id", "agent_runs"),
    ("narrative_memberships", "post_id", "source_posts"),
    ("narrative_memberships", "claim_id", "claims"),
]


def _constraint_name(table: str, column: str) -> str:
    return f"{table}_{column}_fkey"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, column, ref, on_delete in _CASCADE:
        name = _constraint_name(table, column)
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({column}) REFERENCES {ref}(id) ON DELETE {on_delete}"
        )
    for table, column, ref in _SET_NULL:
        name = _constraint_name(table, column)
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({column}) REFERENCES {ref}(id) ON DELETE SET NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, column, ref, _on_delete in _CASCADE:
        name = _constraint_name(table, column)
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({column}) REFERENCES {ref}(id)"
        )
    for table, column, ref in _SET_NULL:
        name = _constraint_name(table, column)
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({column}) REFERENCES {ref}(id)"
        )
