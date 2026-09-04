"""V3 Part B: Workspace Entity Intelligence（WorkspaceEntityService）。

关键语义（审阅修订版 §9.2/§30）：不基于可 retract 的 Alignment confirmed
candidate 做不可逆 merge；正确来源是当前仍存在的 Case-level CanonicalEntity
+ account EntityMention → 可撤销 WorkspaceEntityRelation(same_as)。

refresh_case 固定 13 步流程（§28）；identity_component 只沿 active same_as
边遍历，500 节点硬保护（§31）。
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.application.repositories import ApplicationRepository
from app.core import v3
from app.core.errors import ApplicationError, ResourceNotFoundError
from app.infrastructure.database.alignment_repository import AlignmentRepository
from app.infrastructure.database.engine import Database
from app.infrastructure.database.integrity_repository import IntegrityRepository
from app.infrastructure.database.models import AccountRecord
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.database.workspace_entity_repository import (
    WorkspaceEntityRepository,
)

MAX_COMPONENT_NODES = 500


def _split_key(key_entry: dict[str, str]) -> tuple[str, str]:
    value = key_entry["key_value"]
    platform, _, native_id = value.partition(":")
    return platform, native_id


class SimpleNamespace_account:
    """Case SourcePost 作者的轻量账号视图（无 AccountRecord 行）。"""

    __slots__ = ("id", "platform", "native_id", "name")

    def __init__(
        self, *, id: str, platform: str, native_id: str, name: str
    ) -> None:
        self.id = id
        self.platform = platform
        self.native_id = native_id
        self.name = name


class WorkspaceEntityService:
    def __init__(
        self,
        *,
        workspace_repository: WorkspaceEntityRepository,
        alignment_repository: AlignmentRepository,
        application_repository: ApplicationRepository,
        social_repository: SocialRepository,
        integrity_repository: IntegrityRepository,
        database: Database,
        max_component_nodes: int = MAX_COMPONENT_NODES,
    ) -> None:
        self._workspace = workspace_repository
        self._alignment = alignment_repository
        self._application = application_repository
        self._social = social_repository
        self._integrity = integrity_repository
        self._database = database
        self._max_component_nodes = max_component_nodes

    # ---------------- refresh（§28 固定流程） ----------------

    async def refresh_case(self, case_id: str) -> dict[str, Any]:
        await self._application.get_case(case_id)  # step 1 validate

        # step 2: batch load Case Accounts。
        # AccountRecord 全局唯一（case_id 只记首次观察），因此 Case 的账号
        # appearance = 首次观察在该 case 的账号 ∪ 该 case SourcePost 的作者
        # 账号（V3 E2E-D 同一账号跨 case 的核心场景）。
        account_rows = await self._application.list_accounts(
            case_id=case_id, limit=10000
        )
        seen_keys = {
            (account.platform, account.native_id) for account in account_rows
        }
        post_authors = await self._social.list_case_post_authors(case_id)
        author_only: list[SimpleNamespace_account] = []
        for platform, native_id, name in post_authors:
            if (platform, native_id) in seen_keys:
                continue
            seen_keys.add((platform, native_id))
            author_only.append(
                SimpleNamespace_account(
                    id=f"post-author:{platform}:{native_id}",
                    platform=platform,
                    native_id=native_id,
                    name=name,
                )
            )
        accounts = [*account_rows, *author_only]

        entities_by_account: dict[str, Any] = {}
        created = 0
        aliases_updated = 0
        now = datetime.now(UTC)

        # step 3/4: resolve or create deterministic entities
        for account in accounts:
            entity = await self._resolve_entity_for_account(
                case_id, account, now=now
            )
            if entity is None:
                continue
            entities_by_account[account.id] = entity

        # step 5: expected case links（source_type=account, source_id=account.id）
        expected_links: dict[str, dict[str, Any]] = {}
        for account in accounts:
            entity = entities_by_account.get(account.id)
            if entity is None:
                continue
            expected_links[account.id] = {
                "entity_id": entity.id,
                "metadata": {
                    "platform": account.platform,
                    "native_id": account.native_id,
                    "name": account.name,
                },
            }
        for source_id, payload in expected_links.items():
            await self._workspace.upsert_case_link(
                entity_id=payload["entity_id"],
                case_id=case_id,
                source_type="account",
                source_id=source_id,
                metadata=payload["metadata"],
                method=(
                    "v3_platform_account"
                    if payload["metadata"].get("native_id")
                    else "v3_case_account"
                ),
                seen_at=now,
            )
        links_upserted = len(expected_links)
        expected_link_ids = {
            ("account", source_id) for source_id in expected_links
        }
        links_removed = await self._workspace.reconcile_case_links(
            case_id, expected_link_ids, source_type="account"
        )

        # step 6: 当前 Case 的 canonical account entities + account mentions
        canonical_entities = await self._alignment.list_entities(
            case_id, entity_type="account"
        )
        mentions_by_entity = await self._alignment.list_account_mentions_by_entity(
            case_id
        )
        # step 7: expected active same_as relations（§30）
        accounts_by_id = {account.id: account for account in accounts}
        expected_pairs: dict[tuple[str, str], str] = {}
        for canonical in canonical_entities:
            member_entities: list[str] = []
            for account_id in mentions_by_entity.get(canonical.id, []):
                account = accounts_by_id.get(account_id)
                if account is None:
                    continue
                entity = entities_by_account.get(account.id) or (
                    await self._resolve_entity_for_account(case_id, account, now=now)
                )
                if entity is not None and entity.id not in member_entities:
                    member_entities.append(entity.id)
                    entities_by_account[account.id] = entity
            entity_ids = sorted(member_entities)
            for i in range(len(entity_ids)):
                for j in range(i + 1, len(entity_ids)):
                    expected_pairs[(entity_ids[i], entity_ids[j])] = canonical.id
        relations_upserted = 0
        for (left, right), canonical_id in expected_pairs.items():
            await self._workspace.upsert_relation(
                left_entity_id=left,
                right_entity_id=right,
                relation_type="same_as",
                source_case_id=case_id,
                source_type="canonical_entity",
                source_id=canonical_id,
                method="v3_canonical_same_as",
                seen_at=now,
            )
            relations_upserted += 1
        relations_retracted = await self._workspace.reconcile_case_relations(
            case_id, set(expected_pairs)
        )

        # step 11: orphan cleanup
        orphans_removed = await self._workspace.delete_orphans()

        # step 12: canonical name / aliases / first_seen / last_seen
        for account in accounts:
            entity = entities_by_account.get(account.id)
            if entity is not None:
                changed = await self._update_entity_naming(entity, account)
                aliases_updated += int(changed)

        return {
            "case_id": case_id,
            "accounts": len(accounts),
            "entities": len(set(e.id for e in entities_by_account.values())),
            "entities_created": created,
            "links_upserted": links_upserted,
            "links_removed": links_removed,
            "relations_upserted": relations_upserted,
            "relations_retracted": relations_retracted,
            "orphans_removed": orphans_removed,
            "aliases_updated": aliases_updated,
            "algorithm_version": v3.WORKSPACE_ENTITY_VERSION,
        }

    async def _resolve_entity_for_account(
        self, case_id: str, account: AccountRecord, *, now: datetime
    ) -> Any | None:
        """§29：有 platform+native_id → deterministic key；无 → case-local。"""
        if account.platform and account.native_id:
            key_type = "platform_account"
            key_value = f"{account.platform}:{account.native_id}"
        else:
            key_type = "case_account"
            key_value = f"{case_id}:{account.id}"
        existing = await self._workspace.find_by_key(key_type, key_value)
        if existing is not None:
            return existing
        created = await self._workspace.create_with_key(
            canonical_name=account.name or key_value,
            key_type=key_type,
            key_value=key_value,
            method="v3_platform_account"
            if key_type == "platform_account"
            else "v3_case_account",
        )
        return created

    async def _update_entity_naming(self, entity: Any, account: AccountRecord) -> bool:
        """§7：最新非空 Account.name → canonical_name；旧名进 aliases（≤20）。"""
        new_name = (account.name or "").strip() or entity.canonical_name
        if new_name == entity.canonical_name:
            await self._touch_entity(entity, account)
            return False
        old_name = entity.canonical_name
        aliases = [a for a in (entity.aliases_json or []) if a]
        if old_name and old_name not in aliases:
            aliases.append(old_name)
        aliases = aliases[-v3.MAX_ENTITY_ALIASES :]
        async with self._database.session_factory() as session:
            record = await session.get(type(entity), entity.id)
            if record is None:
                return False
            record.canonical_name = new_name
            record.aliases_json = aliases
            record.last_seen_at = datetime.now(UTC)
            await session.commit()
        return True

    async def _touch_entity(self, entity: Any, account: AccountRecord) -> None:
        async with self._database.session_factory() as session:
            record = await session.get(type(entity), entity.id)
            if record is None:
                return
            record.last_seen_at = datetime.now(UTC)
            await session.commit()

    # ---------------- identity component（§31） ----------------

    async def identity_component(self, entity_id: str) -> dict[str, Any]:
        """沿 active same_as 边 BFS；component_key = min(entity_ids)。"""
        component_ids = await self._component_ids(entity_id)
        return {
            "entity_ids": component_ids,
            "component_key": min(component_ids),
        }

    async def list_components_with_cases(
        self, *, max_entities: int = 5000
    ) -> list[dict[str, Any]]:
        """V3 §53：全局 identity component → 出现过的 Cases 聚合（actor_recurrence 输入）。

        component 按 active same_as 边 BFS（运行时计算，component_key=min
        entity_id）；每个 component 的 Cases 为全部 member entity 的 case
        links 并集。硬保护：entity 上限 max_entities。
        """
        entities = await self._workspace.list(
            entity_type="account", limit=max_entities, offset=0
        )
        if not entities:
            return []
        entity_ids = [entity.id for entity in entities]
        entity_set = set(entity_ids)
        relations = await self._workspace.list_active_relations_for_entities(
            entity_ids
        )
        adjacency: dict[str, set[str]] = defaultdict(set)
        for relation in relations:
            for left, right in (
                (relation.left_entity_id, relation.right_entity_id),
                (relation.right_entity_id, relation.left_entity_id),
            ):
                if left in entity_set and right in entity_set:
                    adjacency[left].add(right)

        components: list[list[str]] = []
        visited: set[str] = set()
        for entity_id in entity_ids:
            if entity_id in visited:
                continue
            stack = [entity_id]
            component: list[str] = []
            visited.add(entity_id)
            while stack:
                current = stack.pop()
                component.append(current)
                for neighbor in adjacency.get(current, ()):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)
            components.append(sorted(component))

        links = await self._workspace.list_case_links_for_entities(entity_ids)
        cases_by_entity: dict[str, set[str]] = defaultdict(set)
        for link in links:
            cases_by_entity[link.entity_id].add(link.case_id)

        results: list[dict[str, Any]] = []
        for component in components:
            component_cases: set[str] = set()
            for entity_id in component:
                component_cases |= cases_by_entity.get(entity_id, set())
            results.append(
                {
                    "component_key": min(component),
                    "entity_ids": component,
                    "cases": sorted(component_cases),
                }
            )
        return results

    async def _component_ids(self, entity_id: str) -> list[str]:
        visited: set[str] = {entity_id}
        queue: deque[str] = deque([entity_id])
        while queue:
            current = queue.popleft()
            relations = await self._workspace.list_active_relations_for_entities(
                [current]
            )
            for relation in relations:
                for neighbor in (
                    relation.left_entity_id,
                    relation.right_entity_id,
                ):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
                        if len(visited) > self._max_component_nodes:
                            raise ApplicationError(
                                "identity component exceeds hard guard "
                                f"({self._max_component_nodes} nodes)",
                                code="identity_component_too_large",
                            )
        return sorted(visited)

    # ---------------- list（§33/§47：列表聚合，批量统计避免 N+1） -------

    async def list_entities(
        self,
        *,
        query: str | None = None,
        platform: str | None = None,
        min_investigations: int = 0,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        entities = await self._workspace.list(
            query=query,
            platform=platform,
            min_investigations=min_investigations,
            limit=min(limit, 50),
            offset=min(offset, 5000),
        )
        entity_ids = [entity.id for entity in entities]
        keys = await self._workspace.list_keys(entity_ids)
        links = await self._workspace.list_case_links_for_entities(entity_ids)
        keys_by_entity: dict[str, list[dict[str, str]]] = {}
        cases_by_entity: dict[str, set[str]] = {}
        identity_by_platform: dict[str, list[str]] = {}
        for key in keys:
            if key.key_type != "platform_account":
                continue
            keys_by_entity.setdefault(key.entity_id, []).append(
                {"key_type": key.key_type, "key_value": key.key_value}
            )
            platform_key, _, native_id = key.key_value.partition(":")
            identity_by_platform.setdefault(platform_key, []).append(native_id)
        for link in links:
            cases_by_entity.setdefault(link.entity_id, set()).add(link.case_id)
        # post/comment 批量计数：(platform, native_id) 全局唯一归属一个 entity
        post_counts, comment_counts = await self._bulk_content_counts(
            identity_by_platform
        )
        # risk summary 批量：一次收集所有相关 case 的精确 subject assessment
        all_case_ids = sorted({c for cases in cases_by_entity.values() for c in cases})
        high_risk_subjects = await self._high_risk_subject_ids(all_case_ids)
        items = []
        for entity in entities:
            platform_keys = keys_by_entity.get(entity.id, [])
            case_ids = sorted(cases_by_entity.get(entity.id, set()))
            subjects = {kv["key_value"] for kv in platform_keys}
            risk_summary = (
                "high" if subjects & high_risk_subjects else None
            )
            items.append(
                {
                    "entity_id": entity.id,
                    "entity_type": entity.entity_type,
                    "canonical_name": entity.canonical_name,
                    "platforms": sorted(
                        {kv["key_value"].partition(":")[0] for kv in platform_keys}
                    ),
                    "investigation_count": len(case_ids),
                    "post_count": sum(
                        post_counts.get(_split_key(kv), 0)
                        for kv in platform_keys
                    ),
                    "comment_count": sum(
                        comment_counts.get(_split_key(kv), 0)
                        for kv in platform_keys
                    ),
                    "last_seen_at": entity.last_seen_at,
                    "risk_summary": risk_summary,
                }
            )
        return {
            "items": items,
            "total": await self._workspace.count(),
        }

    async def list_case_entities(
        self,
        case_id: str,
        *,
        query: str | None = None,
        platform: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """§33：只返回当前 Case 直接出现的 entities。"""
        await self._application.get_case(case_id)
        records = await self._workspace.list_entities_for_case(
            case_id, limit=min(limit + offset, 2000)
        )
        if query:
            lowered = query.lower()
            records = [
                record
                for record in records
                if lowered in (record.canonical_name or "").lower()
            ]
        entities = records[offset : offset + limit]
        entity_ids = [entity.id for entity in entities]
        keys = await self._workspace.list_keys(entity_ids)
        links = await self._workspace.list_case_links_for_entities(entity_ids)
        keys_by_entity: dict[str, list[dict[str, str]]] = {}
        cases_by_entity: dict[str, set[str]] = {}
        identity_by_platform: dict[str, list[str]] = {}
        for key in keys:
            if key.key_type != "platform_account":
                continue
            keys_by_entity.setdefault(key.entity_id, []).append(
                {"key_type": key.key_type, "key_value": key.key_value}
            )
            platform_key, _, native_id = key.key_value.partition(":")
            identity_by_platform.setdefault(platform_key, []).append(native_id)
        for link in links:
            cases_by_entity.setdefault(link.entity_id, set()).add(link.case_id)
        post_counts, comment_counts = await self._bulk_content_counts(
            identity_by_platform
        )
        all_case_ids = sorted({c for cases in cases_by_entity.values() for c in cases})
        high_risk_subjects = await self._high_risk_subject_ids(all_case_ids)
        items = []
        for entity in entities:
            platform_keys = keys_by_entity.get(entity.id, [])
            case_ids = sorted(cases_by_entity.get(entity.id, set()))
            if platform and not any(
                kv["key_value"].startswith(f"{platform}:") for kv in platform_keys
            ):
                continue
            subjects = {kv["key_value"] for kv in platform_keys}
            items.append(
                {
                    "entity_id": entity.id,
                    "entity_type": entity.entity_type,
                    "canonical_name": entity.canonical_name,
                    "platforms": sorted(
                        {kv["key_value"].partition(":")[0] for kv in platform_keys}
                    ),
                    "investigation_count": len(case_ids),
                    "post_count": sum(
                        post_counts.get(_split_key(kv), 0)
                        for kv in platform_keys
                    ),
                    "comment_count": sum(
                        comment_counts.get(_split_key(kv), 0)
                        for kv in platform_keys
                    ),
                    "last_seen_at": entity.last_seen_at,
                    "risk_summary": (
                        "high" if subjects & high_risk_subjects else None
                    ),
                }
            )
        return {"items": items, "total": len(records)}

    async def _high_risk_subject_ids(self, case_ids: Sequence[str]) -> set[str]:
        subjects: set[str] = set()
        for case_id in case_ids:
            rows = await self._integrity.list_assessments(case_id, limit=500)
            for row in rows:
                if (getattr(row, "band", "") or "") in ("high", "critical"):
                    subject_id = getattr(row, "subject_id", "") or ""
                    if subject_id:
                        subjects.add(subject_id)
        return subjects

    async def _bulk_content_counts(
        self, identity_by_platform: dict[str, list[str]]
    ) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], int], dict]:
        from sqlalchemy import func, select

        from app.infrastructure.database.models import (
            SourceCommentRecord,
            SourcePostRecord,
        )

        post_counts: dict[tuple[str, str], int] = {}
        comment_counts: dict[tuple[str, str], int] = {}
        async with self._database.session_factory() as session:
            for platform_id, native_ids in identity_by_platform.items():
                if not native_ids:
                    continue
                post_rows = await session.execute(
                    select(
                        SourcePostRecord.platform,
                        SourcePostRecord.author_id,
                        func.count(SourcePostRecord.id),
                    )
                    .where(
                        SourcePostRecord.platform == platform_id,
                        SourcePostRecord.author_id.in_(tuple(native_ids)),
                    )
                    .group_by(SourcePostRecord.platform, SourcePostRecord.author_id)
                )
                for platform, author_id, count in post_rows.all():
                    post_counts[(str(platform), str(author_id))] = int(count)
                comment_rows = await session.execute(
                    select(
                        SourcePostRecord.platform,
                        SourceCommentRecord.author_id,
                        func.count(SourceCommentRecord.id),
                    )
                    .select_from(SourceCommentRecord)
                    .join(
                        SourcePostRecord,
                        SourceCommentRecord.post_id == SourcePostRecord.id,
                    )
                    .where(
                        SourcePostRecord.platform == platform_id,
                        SourceCommentRecord.author_id.in_(tuple(native_ids)),
                    )
                    .group_by(SourcePostRecord.platform, SourceCommentRecord.author_id)
                )
                for platform, author_id, count in comment_rows.all():
                    comment_counts[(str(platform), str(author_id))] = int(count)
        return post_counts, comment_counts

    async def get_profile(self, entity_id: str) -> dict[str, Any]:
        entity = await self._workspace.get(entity_id)
        if entity is None:
            raise ResourceNotFoundError("workspace entity", entity_id)
        component = await self.identity_component(entity_id)
        entity_ids = component["entity_ids"]
        entities = [await self._workspace.get(eid) for eid in entity_ids]
        entities = [e for e in entities if e is not None]

        # §32.1 canonical display name：component 内 last_seen 最新且非空
        named = [e for e in entities if (e.canonical_name or "").strip()]
        named.sort(key=lambda e: e.last_seen_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        display_name = named[0].canonical_name if named else entity.canonical_name
        aliases: list[str] = []
        for e in entities:
            for alias in [e.canonical_name, *(e.aliases_json or [])]:
                if alias and alias != display_name and alias not in aliases:
                    aliases.append(alias)
        aliases = aliases[: v3.MAX_ENTITY_ALIASES]

        # platform identities（component 内全部 platform_account keys）
        keys = await self._workspace.list_keys(entity_ids)
        platform_keys = [
            {"key_type": k.key_type, "key_value": k.key_value}
            for k in keys
            if k.key_type == "platform_account"
        ]
        identity_by_platform: dict[str, list[str]] = {}
        for k in platform_keys:
            platform, _, native_id = k["key_value"].partition(":")
            identity_by_platform.setdefault(platform, []).append(native_id)

        # case appearances
        links = await self._workspace.list_case_links_for_entities(entity_ids)
        case_ids = sorted({link.case_id for link in links})

        # content stats（一次 IN 查询/平台）
        stats = await self._workspace.content_stats_for_identities(
            identity_by_platform=identity_by_platform, case_ids=case_ids
        )

        # §32.2 Integrity risk：只聚合精确匹配 platform_account key 的 assessment
        exact_subject_ids = {
            k["key_value"] for k in platform_keys
        }
        risk = await self._risk_for_component(case_ids, exact_subject_ids)

        # §32.3 coordination memberships（复用，不重算）
        memberships = await self._coordination_memberships(case_ids, exact_subject_ids)

        return {
            "entity_id": entity_id,
            "component_key": component["component_key"],
            "entity_ids": entity_ids,
            "entity_type": entity.entity_type,
            "canonical_name": display_name,
            "aliases": aliases,
            "platform_identities": platform_keys,
            "investigation_count": len(case_ids),
            "investigations": case_ids,
            "post_count": stats["post_count"],
            "comment_count": stats["comment_count"],
            "engagement_total": stats["engagement_total"],
            "first_seen_at": min(
                (e.first_seen_at for e in entities if e.first_seen_at),
                default=None,
            ),
            "last_seen_at": max(
                (e.last_seen_at for e in entities if e.last_seen_at),
                default=None,
            ),
            "recent_posts": stats["recent_posts"],
            "risk_assessments": risk["assessments"],
            "unresolved_local_risk": risk["unresolved_local_risk"],
            "coordination_memberships": memberships,
            "algorithm_version": v3.WORKSPACE_ENTITY_VERSION,
        }

    async def _risk_for_component(
        self, case_ids: Sequence[str], exact_subject_ids: set[str]
    ) -> dict[str, Any]:
        assessments: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for case_id in case_ids:
            rows = await self._integrity.list_assessments(case_id, limit=500)
            for row in rows:
                subject_id = getattr(row, "subject_id", "") or ""
                entry = {
                    "case_id": case_id,
                    "assessment_id": row.id,
                    "subject_id": subject_id,
                    "risk_level": getattr(row, "band", ""),
                    "score": getattr(row, "score", None),
                }
                if subject_id in exact_subject_ids:
                    assessments.append(entry)
                else:
                    # name-only（platform:author_name）风险不跨 case 自动合并
                    unresolved.append(entry)
        return {"assessments": assessments, "unresolved_local_risk": unresolved}

    async def _coordination_memberships(
        self, case_ids: Sequence[str], exact_subject_ids: set[str]
    ) -> list[dict[str, Any]]:
        memberships: list[dict[str, Any]] = []
        for case_id in case_ids:
            clusters = await self._integrity.list_clusters(case_id)
            for cluster in clusters:
                members = await self._integrity.list_cluster_members(cluster.id)
                matched = [
                    member
                    for member in members
                    if getattr(member, "account_id", "")
                    and member.account_id in exact_subject_ids
                ]
                if not matched:
                    continue
                memberships.append(
                    {
                        "case_id": case_id,
                        "cluster_id": cluster.id,
                        "cluster_size": getattr(cluster, "size", len(members)),
                        "score": getattr(cluster, "score", None),
                        "member_subject_ids": [
                            member.account_id for member in matched
                        ],
                    }
                )
        return memberships
