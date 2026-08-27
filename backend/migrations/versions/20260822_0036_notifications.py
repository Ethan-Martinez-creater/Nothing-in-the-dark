"""Create notification/subscription/share/export tables (13).

调查结果订阅与外部协作：subscriptions / notification_endpoints /
notification_events (Outbox) / delivery_attempts / digest_batches /
share_links / export_jobs。

Revision ID: 20260822_0036
Revises: 20260822_0035
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0036"
down_revision: str | None = "20260822_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("event_filters", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("channel", sa.String(32), nullable=False, server_default="inbox"),
        sa.Column("endpoint_id", sa.String(36), nullable=True),
        sa.Column("schedule", sa.String(24), nullable=False, server_default="instant"),
        sa.Column("quiet_hours", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "notification_endpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("type", sa.String(32), nullable=False, server_default="webhook"),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("secret_ref", sa.String(200), nullable=False, server_default=""),
        sa.Column("allowed_event_types", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("verification_state", sa.String(24), nullable=False, server_default="unverified"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "notification_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="1.0"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("case_id", sa.String(36), nullable=False, index=True),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("classification", sa.String(32), nullable=False, server_default="monitoring"),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("dedupe_key", sa.String(64), nullable=False, server_default=""),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", name="uq_notification_event_id"),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_dedupe"),
    )
    op.create_index("ix_notification_events_event_type", "notification_events", ["event_type"])

    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False, index=True),
        sa.Column("subscription_id", sa.String(36), nullable=False, index=True),
        sa.Column("endpoint_id", sa.String(36), nullable=False, index=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("http_summary", sa.String(200), nullable=False, server_default=""),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", "subscription_id", name="uq_delivery_event_sub"),
    )

    op.create_table(
        "digest_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("subscription_id", sa.String(36), nullable=False, index=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("subscription_id", "window_start", name="uq_digest_window"),
    )

    op.create_table(
        "share_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("target_type", sa.String(32), nullable=False, server_default="artifact"),
        sa.Column("target_id", sa.String(200), nullable=False, server_default=""),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("download_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("token_hash", name="uq_share_token_hash"),
    )
    op.create_index("ix_share_links_token_hash", "share_links", ["token_hash"])

    op.create_table(
        "export_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("scope", sa.String(32), nullable=False, server_default="case"),
        sa.Column("scope_ref", sa.String(200), nullable=False, server_default=""),
        sa.Column("format", sa.String(16), nullable=False, server_default="json"),
        sa.Column("redaction_policy", sa.String(32), nullable=False, server_default="standard"),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("artifact_id", sa.String(36), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("export_jobs")
    op.drop_table("share_links")
    op.drop_table("digest_batches")
    op.drop_table("delivery_attempts")
    op.drop_table("notification_events")
    op.drop_table("notification_endpoints")
    op.drop_table("subscriptions")
