"""Extend media_assets and add the media pipeline tables (04).

真正的多模态采集与解析流水线：media_assets 增加下载/分析状态、真实文件
哈希与 C2PA 字段；新增 media_derivatives（派生文件）、media_transcripts
（OCR/ASR 分段文本）、media_pipeline_jobs（阶段租约任务）。

Revision ID: 20260820_0021
Revises: 20260820_0020
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0021"
down_revision: str | None = "20260820_0020"
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
    op.add_column("media_assets", sa.Column("source_kind", sa.String(32), nullable=False, server_default="url"))
    op.add_column("media_assets", sa.Column("storage_uri", sa.String(500), nullable=True))
    op.add_column("media_assets", sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("media_assets", sa.Column("mime_type", sa.String(100), nullable=False, server_default=""))
    op.add_column("media_assets", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("media_assets", sa.Column("width", sa.Integer(), nullable=True))
    op.add_column("media_assets", sa.Column("height", sa.Integer(), nullable=True))
    op.add_column("media_assets", sa.Column("download_status", sa.String(32), nullable=False, server_default="not_downloaded", index=True))
    op.add_column("media_assets", sa.Column("analysis_status", sa.String(32), nullable=False, server_default="pending", index=True))
    op.add_column("media_assets", sa.Column("error_code", sa.String(64), nullable=True))
    op.add_column("media_assets", sa.Column("actual_sha256", sa.String(64), nullable=True))
    op.add_column("media_assets", sa.Column("hash_kind", sa.String(32), nullable=False, server_default="url_fingerprint_legacy"))
    op.add_column("media_assets", sa.Column("c2pa_status", sa.String(32), nullable=True))
    op.add_column("media_assets", sa.Column("pipeline_version", sa.String(64), nullable=False, server_default="1.0.0"))

    op.create_table(
        "media_derivatives",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("asset_id", sa.String(36), sa.ForeignKey("media_assets.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(32), nullable=False, index=True),
        sa.Column("storage_uri", sa.String(500), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("time_start_ms", sa.Integer(), nullable=True),
        sa.Column("time_end_ms", sa.Integer(), nullable=True),
        sa.Column("bbox", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("producer", sa.String(100), nullable=False, server_default=""),
        sa.Column("version", sa.String(64), nullable=False, server_default="1.0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("asset_id", "kind", "producer", "version", name="uq_media_derivative_asset_kind_producer_version"),
    )

    op.create_table(
        "media_transcripts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("asset_id", sa.String(36), sa.ForeignKey("media_assets.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(32), nullable=False, server_default="asr"),
        sa.Column("language", sa.String(16), nullable=False, server_default=""),
        sa.Column("segments", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("full_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(100), nullable=False, server_default=""),
        sa.Column("version", sa.String(64), nullable=False, server_default="1.0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("asset_id", "kind", "provider", "version", name="uq_media_transcript_asset_kind_provider_version"),
    )

    op.create_table(
        "media_pipeline_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("asset_id", sa.String(36), sa.ForeignKey("media_assets.id"), nullable=False, index=True),
        sa.Column("stage", sa.String(32), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending", index=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resource_stats", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.String(64), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("asset_id", "stage", name="uq_media_pipeline_job_asset_stage"),
    )


def downgrade() -> None:
    op.drop_table("media_pipeline_jobs")
    op.drop_table("media_transcripts")
    op.drop_table("media_derivatives")
    for column in (
        "pipeline_version",
        "c2pa_status",
        "hash_kind",
        "actual_sha256",
        "error_code",
        "analysis_status",
        "download_status",
        "height",
        "width",
        "duration_ms",
        "mime_type",
        "byte_size",
        "storage_uri",
        "source_kind",
    ):
        op.drop_column("media_assets", column)
