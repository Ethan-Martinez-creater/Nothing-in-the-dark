"""Create continuous monitoring & alerting tables.

01 持续监测、观察名单与事件预警：monitor_definitions / monitor_cursors /
monitor_executions / alert_rules / alert_occurrences。调度由独立
MonitorScheduler 执行；游标与执行均带唯一约束，支持多 Worker 竞争与
重启恢复。

Revision ID: 20260820_0020
Revises: 20260809_0019
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0020"
down_revision: str | None = "20260809_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "monitor_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true(), index=True),
        sa.Column("schedule_type", sa.String(32), nullable=False, server_default="interval"),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("cron", sa.String(64), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("query_spec", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("platforms", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("account_watchlist", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("lookback_seconds", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("analysis_policy", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
    )

    op.create_table(
        "monitor_cursors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("monitor_id", sa.String(36), sa.ForeignKey("monitor_definitions.id"), nullable=False, index=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("cursor_payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("last_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("monitor_id", "platform", name="uq_monitor_cursor_monitor_platform"),
    )

    op.create_table(
        "monitor_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("monitor_id", sa.String(36), sa.ForeignKey("monitor_definitions.id"), nullable=False, index=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="scheduled", index=True),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=True, index=True),
        sa.Column("platform_stats", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("monitor_id", "scheduled_at", name="uq_monitor_execution_monitor_scheduled"),
    )

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("monitor_id", sa.String(36), sa.ForeignKey("monitor_definitions.id"), nullable=False, index=True),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
    )

    op.create_table(
        "alert_occurrences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("monitor_id", sa.String(36), sa.ForeignKey("monitor_definitions.id"), nullable=False, index=True),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("alert_rules.id"), nullable=False, index=True),
        sa.Column("fingerprint", sa.String(64), nullable=False, index=True),
        sa.Column("cooldown_bucket", sa.String(32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("trigger_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="open", index=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("metric_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("acknowledged_by", sa.String(100), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("rule_id", "fingerprint", "cooldown_bucket", name="uq_alert_occurrence_rule_fingerprint_bucket"),
    )


def downgrade() -> None:
    op.drop_table("alert_occurrences")
    op.drop_table("alert_rules")
    op.drop_table("monitor_executions")
    op.drop_table("monitor_cursors")
    op.drop_table("monitor_definitions")
