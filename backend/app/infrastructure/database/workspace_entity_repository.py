"""V3 §26: Workspace Entity persistence（全局账号 identity node）。

核心语义（§9 reconciliation / §9.1 reversible relation）：
- WorkspaceEntityCaseLink 是 reconciliation 而非 append-only：refresh 时
  upsert expected、删除 stale；
- WorkspaceEntityRelation(same_as) 可撤销：materialization retract 后
  relation 置 retracted，不做不可逆 merge；
- 并发安全依赖 UNIQUE + IntegrityError reload（§67.1）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    SourceCommentRecord,
    SourcePostRecord,
    WorkspaceEntityCaseLinkRecord,
    WorkspaceEntityKeyRecord,
    WorkspaceEntityRecord,
    WorkspaceEntityRelationRecord,
)


def canonical_pair(left_entity_id: str, right_entity_id: str) -> tuple[str, str]:
    """§9.1 pair canonical ordering：left <= right。"""
    return tuple(sorted((left_entity_id, right_entity_id)))  # type: ignore[return-value]


class WorkspaceEntityRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    # ---------------- read ----------------

    async def get(self, entity_id: str) -> WorkspaceEntityRecord | None:
        async with self._database.session_factory() as session:
            return await session.get(WorkspaceEntityRecord, entity_id)

    async def list(
        self,
        *,
        query: str | None = None,
        platform: str | None = None,
        min_investigations: int = 0,
        entity_type: str = "account",
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[WorkspaceEntityRecord]:
        query_builder = select(WorkspaceEntityRecord).where(
            WorkspaceEntityRecord.status == "active",
            WorkspaceEntityRecord.entity_type == entity_type,
        )
        if query:
            like = f"%{query}%"
            key_match = select(WorkspaceEntityKeyRecord.entity_id).where(
                WorkspaceEntityKeyRecord.key_value.ilike(like)
            )
            query_builder = query_builder.where(
                WorkspaceEntityRecord.canonical_name.ilike(like)
                | WorkspaceEntityRecord.id.in_(key_match)
            )
        if platform:
            key_match = select(WorkspaceEntityKeyRecord.entity_id).where(
                WorkspaceEntityKeyRecord.key_type == "platform_account",
                WorkspaceEntityKeyRecord.key_value.like(f"{platform}:%"),
            )
            query_builder = query_builder.where(
                WorkspaceEntityRecord.id.in_(key_match)
            )
        if min_investigations > 0:
            link_counts = (
                select(
                    WorkspaceEntityCaseLinkRecord.entity_id,
                    func.count(
                        func.distinct(WorkspaceEntityCaseLinkRecord.case_id)
                    ).label("case_count"),
                )
                .group_by(WorkspaceEntityCaseLinkRecord.entity_id)
                .having(
                    func.count(func.distinct(WorkspaceEntityCaseLinkRecord.case_id))
                    >= min_investigations
                )
                .subquery()
            )
            query_builder = query_builder.where(
                WorkspaceEntityRecord.id.in_(
                    select(link_counts.c.entity_id)
                )
            )
        query_builder = (
            query_builder.order_by(
                WorkspaceEntityRecord.last_seen_at.desc().nullslast(),
                WorkspaceEntityRecord.id,
            )
            .limit(limit)
            .offset(offset)
        )
        async with self._database.session_factory() as session:
            return (await session.scalars(query_builder)).all()

    async def count(self, *, entity_type: str = "account") -> int:
        async with self._database.session_factory() as session:
            value = await session.scalar(
                select(func.count(WorkspaceEntityRecord.id)).where(
                    WorkspaceEntityRecord.status == "active",
                    WorkspaceEntityRecord.entity_type == entity_type,
                )
            )
            return int(value or 0)

    async def find_by_key(
        self, key_type: str, key_value: str
    ) -> WorkspaceEntityRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(WorkspaceEntityRecord)
                .join(
                    WorkspaceEntityKeyRecord,
                    WorkspaceEntityKeyRecord.entity_id == WorkspaceEntityRecord.id,
                )
                .where(
                    WorkspaceEntityKeyRecord.key_type == key_type,
                    WorkspaceEntityKeyRecord.key_value == key_value,
                )
            )

    async def get_key(
        self, entity_id: str, key_type: str
    ) -> WorkspaceEntityKeyRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(WorkspaceEntityKeyRecord).where(
                    WorkspaceEntityKeyRecord.entity_id == entity_id,
                    WorkspaceEntityKeyRecord.key_type == key_type,
                )
            )

    async def list_keys(
        self, entity_ids: Sequence[str]
    ) -> Sequence[WorkspaceEntityKeyRecord]:
        if not entity_ids:
            return []
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(WorkspaceEntityKeyRecord).where(
                    WorkspaceEntityKeyRecord.entity_id.in_(tuple(entity_ids))
                )
            )
            return result.all()

    # ---------------- create / link ----------------

    async def create_with_key(
        self,
        *,
        entity_type: str = "account",
        canonical_name: str = "",
        aliases: list[str] | None = None,
        key_type: str | None = None,
        key_value: str | None = None,
        confidence: float = 1.0,
        method: str = "",
        created_by: str = "v3_workspace_refresh",
    ) -> WorkspaceEntityRecord:
        """创建实体并绑定 identity key；UNIQUE 冲突时 reload 既有实体返回。

        并发刷新两个 case 命中同一 platform_account key 时（§29 / §67.1）
        不产生重复节点。
        """
        from app.core.errors import ApplicationError

        try:
            async with self._database.session_factory() as session:
                record = WorkspaceEntityRecord(
                    entity_type=entity_type,
                    canonical_name=canonical_name,
                    aliases_json=list(aliases or []),
                    status="active",
                    created_by=created_by,
                )
                session.add(record)
                await session.flush()
                if key_type and key_value:
                    session.add(
                        WorkspaceEntityKeyRecord(
                            entity_id=record.id,
                            key_type=key_type,
                            key_value=key_value,
                            confidence=confidence,
                            method=method,
                        )
                    )
                await session.commit()
                await session.refresh(record)
                return record
        except IntegrityError:
            if not (key_type and key_value):
                raise
            existing = await self.find_by_key(key_type, key_value)
            if existing is not None:
                return existing
            raise ApplicationError(
                f"workspace entity key conflict without existing entity: "
                f"{key_type}:{key_value}",
                code="workspace_entity_key_conflict",
            ) from None

    async def upsert_case_link(
        self,
        *,
        entity_id: str,
        case_id: str,
        source_type: str,
        source_id: str,
        confidence: float = 1.0,
        method: str = "",
        metadata: dict[str, Any] | None = None,
        seen_at: datetime | None = None,
    ) -> WorkspaceEntityCaseLinkRecord:
        """reconciliation upsert：UNIQUE(case_id, source_type, source_id)。"""
        seen = seen_at or datetime.now(UTC)
        async with self._database.session_factory() as session:
            link = await session.scalar(
                select(WorkspaceEntityCaseLinkRecord).where(
                    WorkspaceEntityCaseLinkRecord.case_id == case_id,
                    WorkspaceEntityCaseLinkRecord.source_type == source_type,
                    WorkspaceEntityCaseLinkRecord.source_id == source_id,
                )
            )
            if link is None:
                link = WorkspaceEntityCaseLinkRecord(
                    entity_id=entity_id,
                    case_id=case_id,
                    source_type=source_type,
                    source_id=source_id,
                    confidence=confidence,
                    method=method,
                    metadata_json=dict(metadata or {}),
                    first_seen_at=seen,
                    last_seen_at=seen,
                )
                session.add(link)
                try:
                    await session.commit()
                    await session.refresh(link)
                    return link
                except IntegrityError:
                    await session.rollback()
                    link = await session.scalar(
                        select(WorkspaceEntityCaseLinkRecord).where(
                            WorkspaceEntityCaseLinkRecord.case_id == case_id,
                            WorkspaceEntityCaseLinkRecord.source_type
                            == source_type,
                            WorkspaceEntityCaseLinkRecord.source_id == source_id,
                        )
                    )
                    if link is None:
                        raise
            if link.entity_id != entity_id:
                # account 被重解析到另一实体（如 key 竞争后收敛）：迁移链接
                link.entity_id = entity_id
            link.confidence = confidence
            link.method = method
            link.metadata_json = dict(metadata or {})
            link.last_seen_at = seen
            await session.commit()
            await session.refresh(link)
            return link

    async def reconcile_case_links(
        self,
        case_id: str,
        expected_source_ids: set[tuple[str, str]],
        *,
        source_type: str = "account",
    ) -> int:
        """删除当前 case 中本次 expected 不再包含的 stale links（§9）。"""
        async with self._database.session_factory() as session:
            current = (
                await session.scalars(
                    select(WorkspaceEntityCaseLinkRecord).where(
                        WorkspaceEntityCaseLinkRecord.case_id == case_id,
                        WorkspaceEntityCaseLinkRecord.source_type == source_type,
                    )
                )
            ).all()
            removed = 0
            for link in current:
                if (link.source_type, link.source_id) not in expected_source_ids:
                    await session.delete(link)
                    removed += 1
            if removed:
                await session.commit()
            return removed

    # ---------------- reversible same_as relations ----------------

    async def upsert_relation(
        self,
        *,
        left_entity_id: str,
        right_entity_id: str,
        relation_type: str,
        source_case_id: str,
        source_type: str,
        source_id: str,
        confidence: float = 1.0,
        method: str = "",
        seen_at: datetime | None = None,
    ) -> WorkspaceEntityRelationRecord:
        """upsert active same_as relation；已 retracted 的恢复为 active。"""
        left, right = canonical_pair(left_entity_id, right_entity_id)
        seen = seen_at or datetime.now(UTC)
        async with self._database.session_factory() as session:
            relation = await session.scalar(
                select(WorkspaceEntityRelationRecord).where(
                    WorkspaceEntityRelationRecord.source_case_id == source_case_id,
                    WorkspaceEntityRelationRecord.left_entity_id == left,
                    WorkspaceEntityRelationRecord.right_entity_id == right,
                    WorkspaceEntityRelationRecord.relation_type == relation_type,
                )
            )
            if relation is None:
                relation = WorkspaceEntityRelationRecord(
                    left_entity_id=left,
                    right_entity_id=right,
                    relation_type=relation_type,
                    status="active",
                    source_case_id=source_case_id,
                    source_type=source_type,
                    source_id=source_id,
                    confidence=confidence,
                    method=method,
                    first_seen_at=seen,
                    last_seen_at=seen,
                )
                session.add(relation)
                try:
                    await session.commit()
                    await session.refresh(relation)
                    return relation
                except IntegrityError:
                    await session.rollback()
                    relation = await session.scalar(
                        select(WorkspaceEntityRelationRecord).where(
                            WorkspaceEntityRelationRecord.source_case_id
                            == source_case_id,
                            WorkspaceEntityRelationRecord.left_entity_id == left,
                            WorkspaceEntityRelationRecord.right_entity_id == right,
                            WorkspaceEntityRelationRecord.relation_type
                            == relation_type,
                        )
                    )
                    if relation is None:
                        raise
            if relation.status != "active":
                relation.status = "active"
            relation.confidence = confidence
            relation.method = method
            relation.last_seen_at = seen
            await session.commit()
            await session.refresh(relation)
            return relation

    async def reconcile_case_relations(
        self,
        source_case_id: str,
        expected_pairs: set[tuple[str, str]],
        *,
        relation_type: str = "same_as",
    ) -> int:
        """把该 case 来源、本次 expected 不再包含的 relation 置 retracted（§30）。"""
        async with self._database.session_factory() as session:
            current = (
                await session.scalars(
                    select(WorkspaceEntityRelationRecord).where(
                        WorkspaceEntityRelationRecord.source_case_id
                        == source_case_id,
                        WorkspaceEntityRelationRecord.relation_type == relation_type,
                        WorkspaceEntityRelationRecord.status == "active",
                    )
                )
            ).all()
            retracted = 0
            for relation in current:
                pair = (relation.left_entity_id, relation.right_entity_id)
                if pair not in expected_pairs:
                    relation.status = "retracted"
                    retracted += 1
            if retracted:
                await session.commit()
            return retracted

    async def list_active_relations_for_entities(
        self, entity_ids: Sequence[str]
    ) -> Sequence[WorkspaceEntityRelationRecord]:
        if not entity_ids:
            return []
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(WorkspaceEntityRelationRecord).where(
                    WorkspaceEntityRelationRecord.status == "active",
                    WorkspaceEntityRelationRecord.relation_type == "same_as",
                    (
                        WorkspaceEntityRelationRecord.left_entity_id.in_(
                            tuple(entity_ids)
                        )
                        | WorkspaceEntityRelationRecord.right_entity_id.in_(
                            tuple(entity_ids)
                        )
                    ),
                )
            )
            return result.all()

    # ---------------- case links 读取 ----------------

    async def list_case_links(
        self,
        entity_id: str,
        *,
        case_id: str | None = None,
    ) -> Sequence[WorkspaceEntityCaseLinkRecord]:
        query = select(WorkspaceEntityCaseLinkRecord).where(
            WorkspaceEntityCaseLinkRecord.entity_id == entity_id
        )
        if case_id is not None:
            query = query.where(WorkspaceEntityCaseLinkRecord.case_id == case_id)
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def list_case_links_for_entities(
        self, entity_ids: Sequence[str]
    ) -> Sequence[WorkspaceEntityCaseLinkRecord]:
        if not entity_ids:
            return []
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(WorkspaceEntityCaseLinkRecord).where(
                    WorkspaceEntityCaseLinkRecord.entity_id.in_(tuple(entity_ids))
                )
            )
            return result.all()

    async def list_entities_for_case(
        self,
        case_id: str,
        *,
        entity_type: str = "account",
        limit: int = 2000,
    ) -> Sequence[WorkspaceEntityRecord]:
        query = (
            select(WorkspaceEntityRecord)
            .join(
                WorkspaceEntityCaseLinkRecord,
                WorkspaceEntityCaseLinkRecord.entity_id == WorkspaceEntityRecord.id,
            )
            .where(
                WorkspaceEntityCaseLinkRecord.case_id == case_id,
                WorkspaceEntityRecord.status == "active",
                WorkspaceEntityRecord.entity_type == entity_type,
            )
            .distinct()
            .order_by(WorkspaceEntityRecord.id)
            .limit(limit)
        )
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    # ---------------- orphan cleanup（§9 / §67 step 8） ----------------

    async def delete_orphans(self) -> int:
        """删除 0 case link 且 0 active relation 的实体。"""
        async with self._database.session_factory() as session:
            links = select(WorkspaceEntityCaseLinkRecord.entity_id).scalar_subquery()
            active_relations = (
                select(WorkspaceEntityRelationRecord.left_entity_id)
                .where(WorkspaceEntityRelationRecord.status == "active")
                .scalar_subquery()
            )
            active_relations_right = (
                select(WorkspaceEntityRelationRecord.right_entity_id)
                .where(WorkspaceEntityRelationRecord.status == "active")
                .scalar_subquery()
            )
            orphans = (
                await session.scalars(
                    select(WorkspaceEntityRecord.id).where(
                        WorkspaceEntityRecord.id.not_in(links),
                        WorkspaceEntityRecord.id.not_in(active_relations),
                        WorkspaceEntityRecord.id.not_in(active_relations_right),
                    )
                )
            ).all()
            for entity_id in orphans:
                record = await session.get(WorkspaceEntityRecord, entity_id)
                if record is not None:
                    await session.delete(record)
            if orphans:
                await session.commit()
            return len(orphans)

    # ---------------- profile 聚合（§32；本 Repository 允许直接查询） -------

    async def content_stats_for_identities(
        self,
        *,
        identity_by_platform: dict[str, list[str]],
        case_ids: Sequence[str],
    ) -> dict[str, Any]:
        """component 内 platform_account 的 post/comment 统计与近期帖子。

        identity_by_platform: {platform: [native_id, ...]}；一次 IN 查询，
        禁止 N+1。author 匹配：SourcePost.author_id == native_id。
        """
        empty: dict[str, Any] = {
            "post_count": 0,
            "comment_count": 0,
            "engagement_total": 0,
            "recent_posts": [],
        }
        if not identity_by_platform or not case_ids:
            return empty
        post_count = 0
        comment_count = 0
        engagement_total = 0
        recent: list[dict[str, Any]] = []
        async with self._database.session_factory() as session:
            for platform, native_ids in identity_by_platform.items():
                if not native_ids:
                    continue
                post_count += int(
                    await session.scalar(
                        select(func.count(SourcePostRecord.id)).where(
                            SourcePostRecord.platform == platform,
                            SourcePostRecord.author_id.in_(tuple(native_ids)),
                            SourcePostRecord.case_id.in_(tuple(case_ids)),
                        )
                    )
                    or 0
                )
                comment_count += int(
                    await session.scalar(
                        select(func.count(SourceCommentRecord.id))
                        .select_from(SourceCommentRecord)
                        .join(
                            SourcePostRecord,
                            SourceCommentRecord.post_id == SourcePostRecord.id,
                        )
                        .where(
                            SourcePostRecord.platform == platform,
                            SourceCommentRecord.author_id.in_(tuple(native_ids)),
                            SourcePostRecord.case_id.in_(tuple(case_ids)),
                        )
                    )
                    or 0
                )
                rows = (
                    await session.execute(
                        select(
                            SourcePostRecord.id,
                            SourcePostRecord.platform,
                            SourcePostRecord.title,
                            SourcePostRecord.content,
                            SourcePostRecord.published_at,
                            SourcePostRecord.engagement,
                            SourcePostRecord.case_id,
                        )
                        .where(
                            SourcePostRecord.platform == platform,
                            SourcePostRecord.author_id.in_(tuple(native_ids)),
                            SourcePostRecord.case_id.in_(tuple(case_ids)),
                        )
                        .order_by(SourcePostRecord.published_at.desc().nullslast())
                        .limit(20)
                    )
                ).all()
                for row in rows:
                    engagement = row.engagement if isinstance(row.engagement, dict) else {}
                    engagement_total += sum(
                        value
                        for value in engagement.values()
                        if isinstance(value, (int, float))
                    )
                    recent.append(
                        {
                            "post_id": row.id,
                            "case_id": row.case_id,
                            "platform": row.platform,
                            "title": row.title,
                            "excerpt": (row.content or "")[:200],
                            "published_at": row.published_at,
                        }
                    )
        recent.sort(
            key=lambda item: item.get("published_at")
            or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return {
            "post_count": post_count,
            "comment_count": comment_count,
            "engagement_total": engagement_total,
            "recent_posts": recent[:20],
        }
