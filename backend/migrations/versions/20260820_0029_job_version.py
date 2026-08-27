"""Add pipeline_version to media_pipeline_jobs (MEDIA-P1-06).

阶段任务唯一键加入 pipeline version，模型/流水线升级后可用新版本重跑同阶段。

Revision ID: 20260820_0029
Revises: 20260820_0028
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0029"
down_revision: str | None = "20260820_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media_pipeline_jobs",
        sa.Column("pipeline_version", sa.String(64), nullable=False, server_default="1.0.0"),
    )
    op.drop_constraint(
        "uq_media_pipeline_job_asset_stage",
        "media_pipeline_jobs",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_media_pipeline_job_asset_stage_version",
        "media_pipeline_jobs",
        ["asset_id", "stage", "pipeline_version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_media_pipeline_job_asset_stage_version",
        "media_pipeline_jobs",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_media_pipeline_job_asset_stage",
        "media_pipeline_jobs",
        ["asset_id", "stage"],
    )
    op.drop_column("media_pipeline_jobs", "pipeline_version")
