"""M3: Collection Definition persistence (versioned, one active per case)."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, update

from app.core.errors import ApplicationError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import CollectionDefinitionRecord


class CollectionRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(
        self, record: CollectionDefinitionRecord
    ) -> CollectionDefinitionRecord:
        try:
            async with self._database.session_factory() as session:
                session.add(record)
                await session.commit()
                await session.refresh(record)
        except Exception as exc:  # noqa: BLE001 - dialect integrity errors vary
            message = str(exc).lower()
            if "uq_collection_case_version" in message or "unique" in message:
                raise ApplicationError(
                    "collection version conflict, retry creation",
                    code="collection_version_conflict",
                ) from exc
            raise
        return record

    async def list_for_case(self, case_id: str) -> Sequence[CollectionDefinitionRecord]:
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(CollectionDefinitionRecord)
                .where(CollectionDefinitionRecord.case_id == case_id)
                .order_by(CollectionDefinitionRecord.version.desc())
            )
            return result.scalars().all()

    async def get(self, definition_id: str) -> CollectionDefinitionRecord | None:
        async with self._database.session_factory() as session:
            return await session.get(CollectionDefinitionRecord, definition_id)

    async def get_active(self, case_id: str) -> CollectionDefinitionRecord | None:
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(CollectionDefinitionRecord).where(
                    CollectionDefinitionRecord.case_id == case_id,
                    CollectionDefinitionRecord.status == "active",
                )
            )
            return result.scalars().first()

    async def max_version(self, case_id: str) -> int:
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(CollectionDefinitionRecord.version).where(
                    CollectionDefinitionRecord.case_id == case_id
                )
            )
            return max((int(v) for v in result.scalars().all()), default=0)

    async def activate(
        self, case_id: str, definition_id: str
    ) -> CollectionDefinitionRecord:
        """单事务激活：校验 draft → 旧 active 置 superseded → 目标置 active。"""
        async with self._database.session_factory() as session:
            target = await session.get(CollectionDefinitionRecord, definition_id)
            if target is None:
                raise ApplicationError(
                    f"collection definition '{definition_id}' does not exist",
                    code="collection_not_found",
                )
            if target.case_id != case_id:
                raise ApplicationError(
                    "collection definition belongs to another case",
                    code="collection_scope_mismatch",
                )
            if target.status != "draft":
                raise ApplicationError(
                    f"collection definition '{definition_id}' is not a draft",
                    code="collection_not_draft",
                )
            try:
                await session.execute(
                    update(CollectionDefinitionRecord)
                    .where(
                        CollectionDefinitionRecord.case_id == case_id,
                        CollectionDefinitionRecord.status == "active",
                    )
                    .values(status="superseded")
                )
                target.status = "active"
                await session.commit()
                await session.refresh(target)
            except Exception as exc:  # noqa: BLE001
                raise ApplicationError(
                    "collection activation conflict",
                    code="collection_activation_conflict",
                ) from exc
            return target
