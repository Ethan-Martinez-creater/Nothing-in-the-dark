"""Extend memories with governance fields and audit tables (23).

记忆安全与用户可控治理：为 memories 增加 memory_type / trust_level /
review_state / confidence_level / valid_from / expires_at / last_verified_at /
content_hash / version / sensitivity / index_status / embedding_version /
write_policy_version / status；旧数据回填为 legacy_unreviewed；新增
memory_access_events / memory_mutations / memory_conflicts。

Revision ID: 20260823_0043
Revises: 20260823_0042
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0043"
down_revision: str | None = "20260823_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("memory_type", sa.String(40), nullable=False, server_default="case_fact"))
    op.add_column("memories", sa.Column("trust_level", sa.String(32), nullable=False, server_default="external_content"))
    op.add_column("memories", sa.Column("review_state", sa.String(32), nullable=False, server_default="unreviewed"))
    op.add_column("memories", sa.Column("confidence_level", sa.String(16), nullable=False, server_default="medium"))
    op.add_column("memories", sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memories", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memories", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memories", sa.Column("content_hash", sa.String(64), nullable=False, server_default=""))
    op.add_column("memories", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("memories", sa.Column("sensitivity", sa.String(16), nullable=False, server_default="low"))
    # indexed / pending / removed（索引状态）
    op.add_column("memories", sa.Column("index_status", sa.String(16), nullable=False, server_default="pending"))
    op.add_column("memories", sa.Column("embedding_version", sa.String(32), nullable=False, server_default=""))
    op.add_column("memories", sa.Column("write_policy_version", sa.String(32), nullable=False, server_default=""))
    # active / pending_review / superseded / expired / disabled / deleted
    op.add_column("memories", sa.Column("status", sa.String(24), nullable=False, server_default="active"))
    op.create_index("ix_memories_status", "memories", ["status"])
    op.create_index("ix_memories_expires_at", "memories", ["expires_at"])
    op.create_index("ix_memories_content_hash", "memories", ["content_hash"])

    # ---- 旧数据回填（legacy_unreviewed）：不自行提升信任等级 ----
    op.execute(
        """
        UPDATE memories
        SET memory_type = CASE kind
            WHEN 'fact' THEN 'case_fact'
            WHEN 'constraint' THEN 'operator_preference'
            WHEN 'preference' THEN 'operator_preference'
            WHEN 'correction' THEN 'case_fact'
            WHEN 'summary' THEN 'conversation_summary'
            WHEN 'platform_profile' THEN 'case_hypothesis'
            ELSE 'external_excerpt'
        END,
        trust_level = CASE source_type
            WHEN 'conversation' THEN 'generated_content'
            WHEN 'constraint' THEN 'operator_input'
            ELSE 'external_content'
        END,
        review_state = 'legacy_unreviewed',
        confidence_level = CASE
            WHEN confidence >= 0.7 THEN 'high'
            WHEN confidence >= 0.4 THEN 'medium'
            ELSE 'low'
        END,
        valid_from = created_at,
        content_hash = md5(content),
        version = 1,
        sensitivity = 'low',
        index_status = CASE WHEN embedding IS NULL THEN 'pending' ELSE 'indexed' END,
        write_policy_version = '1.0',
        status = CASE WHEN active = true THEN 'active' ELSE 'disabled' END
        """
    )

    op.create_table(
        "memory_access_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("memory_id", sa.String(36), sa.ForeignKey("memories.id"), nullable=False, index=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=True, index=True),
        sa.Column("purpose", sa.String(64), nullable=False, server_default=""),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "memory_mutations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("memory_id", sa.String(36), sa.ForeignKey("memories.id"), nullable=False, index=True),
        # create / correct / disable / restore / delete / review / reindex / expire
        sa.Column("action", sa.String(24), nullable=False, server_default="create"),
        sa.Column("actor", sa.String(100), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("from_status", sa.String(24), nullable=False, server_default=""),
        sa.Column("to_status", sa.String(24), nullable=False, server_default=""),
        sa.Column("version_before", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version_after", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "memory_conflicts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("memory_id", sa.String(36), sa.ForeignKey("memories.id"), nullable=False, index=True),
        sa.Column("conflicting_memory_id", sa.String(36), sa.ForeignKey("memories.id"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolved_by", sa.String(36), nullable=True),
        sa.Column("resolution", sa.String(32), nullable=False, server_default=""),
        # pending / supersede_left / supersede_right / reject_both
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("memory_id", "conflicting_memory_id", name="uq_memory_conflict_pair"),
    )


def downgrade() -> None:
    op.drop_table("memory_conflicts")
    op.drop_table("memory_mutations")
    op.drop_table("memory_access_events")
    op.drop_index("ix_memories_content_hash", table_name="memories")
    op.drop_index("ix_memories_expires_at", table_name="memories")
    op.drop_index("ix_memories_status", table_name="memories")
    op.drop_column("memories", "status")
    op.drop_column("memories", "write_policy_version")
    op.drop_column("memories", "embedding_version")
    op.drop_column("memories", "index_status")
    op.drop_column("memories", "sensitivity")
    op.drop_column("memories", "version")
    op.drop_column("memories", "content_hash")
    op.drop_column("memories", "last_verified_at")
    op.drop_column("memories", "expires_at")
    op.drop_column("memories", "valid_from")
    op.drop_column("memories", "confidence_level")
    op.drop_column("memories", "review_state")
    op.drop_column("memories", "trust_level")
    op.drop_column("memories", "memory_type")
