"""Create resilience tables: health, circuit breakers, dead letters, incidents (22).

故障隔离、降级与事故处置：dependency_health / circuit_breaker_states /
retry_attempts / dead_letter_items / incident_records / kill_switches。

Revision ID: 20260823_0042
Revises: 20260823_0041
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0042"
down_revision: str | None = "20260823_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dependency_health",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dependency", sa.String(120), nullable=False, server_default=""),
        sa.Column("scope", sa.String(64), nullable=False, server_default=""),
        # healthy / degraded / outage / auth_required / policy_denied
        sa.Column("status", sa.String(24), nullable=False, server_default="healthy"),
        sa.Column("error_code", sa.String(100), nullable=False, server_default=""),
        # closed / open / half_open
        sa.Column("circuit_state", sa.String(24), nullable=False, server_default="closed"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("dependency", "scope", name="uq_dependency_health_scope"),
    )

    op.create_table(
        "circuit_breaker_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dependency", sa.String(120), nullable=False, server_default=""),
        sa.Column("scope", sa.String(64), nullable=False, server_default=""),
        # closed / open / half_open
        sa.Column("state", sa.String(24), nullable=False, server_default="closed"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("half_open_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("dependency", "scope", name="uq_breaker_dependency_scope"),
    )

    op.create_table(
        "retry_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("operation_key", sa.String(500), nullable=False, server_default=""),
        sa.Column("dependency", sa.String(120), nullable=False, server_default=""),
        sa.Column("scope", sa.String(64), nullable=False, server_default=""),
        # transient / rate_limited / auth_required / permanent_input /
        # policy_denied / resource_exhausted / dependency_outage / unknown
        sa.Column("error_classification", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("error_code", sa.String(100), nullable=False, server_default=""),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("backoff_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("retry_after_seconds", sa.Float(), nullable=True),
        # pending / succeeded / failed / permanent / dead_lettered
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("first_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("first_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("operation_key", name="uq_retry_attempt_operation_key"),
    )

    op.create_table(
        "dead_letter_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("operation_key", sa.String(500), nullable=False, server_default=""),
        sa.Column("dependency", sa.String(120), nullable=False, server_default=""),
        sa.Column("scope", sa.String(64), nullable=False, server_default=""),
        sa.Column("error_classification", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("error_code", sa.String(100), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("policy_version", sa.String(32), nullable=False, server_default=""),
        sa.Column("code_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("recovery_hint", sa.Text(), nullable=False, server_default=""),
        # 敏感 payload 只保留引用，不落原文。
        sa.Column("payload_ref", sa.String(500), nullable=False, server_default=""),
        # pending / approved / retrying / resolved / discarded
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Index("ix_dead_letter_status", "status"),
    )

    op.create_table(
        "incident_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        # info / warning / critical
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        # open / closed
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("impact", sa.Text(), nullable=False, server_default=""),
        sa.Column("timeline_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("metrics_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("actions_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("recovery_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("retro_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("kill_switch_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Index("ix_incident_status", "status"),
    )

    op.create_table(
        "kill_switches",
        sa.Column("id", sa.String(36), primary_key=True),
        # global / platform / tool / dependency
        sa.Column("scope", sa.String(24), nullable=False, server_default="global"),
        sa.Column("target", sa.String(120), nullable=False, server_default="*"),
        # on / off
        sa.Column("status", sa.String(8), nullable=False, server_default="off"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(100), nullable=False, server_default=""),
        sa.Column("approval_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("scope", "target", name="uq_kill_switch_scope_target"),
    )


def downgrade() -> None:
    op.drop_table("kill_switches")
    op.drop_table("incident_records")
    op.drop_table("dead_letter_items")
    op.drop_table("retry_attempts")
    op.drop_table("circuit_breaker_states")
    op.drop_table("dependency_health")
