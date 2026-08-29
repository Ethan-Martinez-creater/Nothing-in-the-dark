"""M3: Collection Definition service (version allocation, generate, activate).

只负责采集定义的产品状态机与校验；不负责实际 crawler 调用。生成逻辑复用
Harness 现有 ``generate_platform_keywords``（LLM 失败/未配置时每平台回退
case.topic），生成结果一律落为 draft，不自动激活。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ApplicationError
from app.harness.search_optimizer import generate_platform_keywords
from app.infrastructure.database.collection_repository import CollectionRepository
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import CaseRecord, CollectionDefinitionRecord
from app.services.collection_filters import validate_collection_filters

_MAX_QUERY_LENGTH = 200


def _clean_str_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _clean_queries(platforms: list[str], raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, list[str]] = {}
    for platform, queries in raw.items():
        if platform not in platforms:
            continue
        entries: list[str] = []
        for query in _clean_str_list(queries):
            if len(query) > _MAX_QUERY_LENGTH:
                query = query[:_MAX_QUERY_LENGTH]
            if query not in entries:
                entries.append(query)
        if entries:
            cleaned[platform] = entries
    return cleaned


class CollectionDefinitionService:
    def __init__(self, database: Database, llm: Any = None) -> None:
        self._database = database
        self._llm = llm
        self._repository = CollectionRepository(database)

    # ---------------- 查询 ----------------

    async def list_for_case(self, case_id: str) -> list[CollectionDefinitionRecord]:
        return list(await self._repository.list_for_case(case_id))

    async def get_for_case(
        self, case_id: str, definition_id: str
    ) -> CollectionDefinitionRecord:
        record = await self._repository.get(definition_id)
        if record is None:
            raise ApplicationError(
                f"collection definition '{definition_id}' does not exist",
                code="collection_not_found",
            )
        if record.case_id != case_id:
            raise ApplicationError(
                "collection definition belongs to another case",
                code="collection_scope_mismatch",
            )
        return record

    async def get_active(self, case_id: str) -> CollectionDefinitionRecord | None:
        return await self._repository.get_active(case_id)

    # ---------------- 创建 / 生成 / 修订 / 激活 ----------------

    async def _load_case(self, case_id: str) -> CaseRecord:
        async with self._database.session_factory() as session:
            case = await session.get(CaseRecord, case_id)
        if case is None:
            raise ApplicationError(
                f"case '{case_id}' does not exist", code="collection_scope_mismatch"
            )
        return case

    async def create_manual(
        self,
        case_id: str,
        *,
        goal: str,
        platforms: list[str],
        platform_queries: dict[str, list[str]] | None = None,
        exclusions: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> CollectionDefinitionRecord:
        case = await self._load_case(case_id)
        allowed = list(case.platforms or [])
        clean_platforms = [p for p in _clean_str_list(platforms) if p in allowed]
        if not clean_platforms:
            raise ApplicationError(
                "platforms must be a non-empty subset of the case platforms",
                code="collection_validation_failed",
            )
        if not goal.strip():
            raise ApplicationError(
                "goal must not be empty", code="collection_validation_failed"
            )
        # C6：未知 filter key 在保存时就拒绝（不出现"保存成功、运行时忽略"）
        validate_collection_filters(filters)
        record = CollectionDefinitionRecord(
            case_id=case_id,
            version=await self._repository.max_version(case_id) + 1,
            status="draft",
            goal=goal.strip(),
            platforms=clean_platforms,
            platform_queries=_clean_queries(clean_platforms, platform_queries),
            exclusions=_clean_str_list(exclusions),
            filters=dict(filters or {}),
        )
        return await self._repository.create(record)

    async def generate(
        self,
        case_id: str,
        *,
        goal: str | None = None,
        generated_by_run_id: str | None = None,
    ) -> CollectionDefinitionRecord:
        case = await self._load_case(case_id)
        platforms = list(case.platforms or [])
        if not platforms:
            raise ApplicationError(
                "case has no platforms to collect", code="collection_validation_failed"
            )
        queries = await generate_platform_keywords(
            self._llm, case.topic, platforms
        )
        generated_by = "llm" if self._llm is not None else "fallback"
        if queries == {platform: [case.topic] for platform in platforms}:
            generated_by = "fallback"
        record = CollectionDefinitionRecord(
            case_id=case_id,
            version=await self._repository.max_version(case_id) + 1,
            status="draft",
            goal=(goal or case.topic).strip(),
            platforms=platforms,
            platform_queries=queries,
            exclusions=[],
            filters={"generated_by": generated_by},
            generated_by_run_id=generated_by_run_id,
        )
        return await self._repository.create(record)

    async def revise(
        self,
        case_id: str,
        definition_id: str,
        *,
        goal: str | None = None,
        platforms: list[str] | None = None,
        platform_queries: dict[str, list[str]] | None = None,
        exclusions: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> CollectionDefinitionRecord:
        """修订 = 基于既有版本创建新 draft，不 PATCH 历史版本。"""
        previous = await self.get_for_case(case_id, definition_id)
        case = await self._load_case(case_id)
        allowed = list(case.platforms or [])
        clean_platforms = _clean_str_list(
            platforms if platforms is not None else list(previous.platforms or [])
        )
        clean_platforms = [p for p in clean_platforms if p in allowed]
        if not clean_platforms:
            raise ApplicationError(
                "platforms must be a non-empty subset of the case platforms",
                code="collection_validation_failed",
            )
        new_goal = (goal if goal is not None else previous.goal).strip()
        if not new_goal:
            raise ApplicationError(
                "goal must not be empty", code="collection_validation_failed"
            )
        filters = dict(filters) if filters is not None else dict(previous.filters or {})
        validate_collection_filters(filters)
        record = CollectionDefinitionRecord(
            case_id=case_id,
            version=await self._repository.max_version(case_id) + 1,
            status="draft",
            goal=new_goal,
            platforms=clean_platforms,
            platform_queries=_clean_queries(
                clean_platforms,
                platform_queries
                if platform_queries is not None
                else previous.platform_queries,
            ),
            exclusions=_clean_str_list(
                exclusions if exclusions is not None else list(previous.exclusions or [])
            ),
            filters=filters,
        )
        return await self._repository.create(record)

    async def activate(
        self, case_id: str, definition_id: str
    ) -> CollectionDefinitionRecord:
        target = await self.get_for_case(case_id, definition_id)
        if not (target.goal or "").strip():
            raise ApplicationError(
                "active definition goal must not be empty",
                code="collection_validation_failed",
            )
        return await self._repository.activate(case_id, definition_id)

    # ---------------- crawl 关键词投影 ----------------

    def keywords_for(
        self,
        definition: CollectionDefinitionRecord,
        requested_platforms: list[str],
        fallback_topic: str,
    ) -> dict[str, list[str]]:
        """把 active definition 投影为请求平台的 keywords。

        请求平台与定义平台取交集；请求包含定义未覆盖的平台时该平台回退
        topic（不静默丢平台）。
        """
        definition_queries = dict(definition.platform_queries or {})
        result: dict[str, list[str]] = {}
        for platform in requested_platforms:
            queries = definition_queries.get(platform)
            if queries:
                result[platform] = list(queries)
            else:
                result[platform] = [fallback_topic]
        return result
