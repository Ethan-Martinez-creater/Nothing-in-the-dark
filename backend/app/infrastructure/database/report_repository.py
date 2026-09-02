"""M7: Report Document persistence（产品层可编辑/可发布报告）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, update

from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import ReportDocumentRecord


class ReportDocumentRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(self, record: ReportDocumentRecord) -> ReportDocumentRecord:
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get(self, report_id: str) -> ReportDocumentRecord | None:
        async with self._database.session_factory() as session:
            return await session.get(ReportDocumentRecord, report_id)

    async def list_for_case(
        self,
        case_id: str,
        *,
        report_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[ReportDocumentRecord]:
        query = select(ReportDocumentRecord).where(
            ReportDocumentRecord.case_id == case_id
        )
        if report_id:
            query = query.where(ReportDocumentRecord.id == report_id)
        if status:
            query = query.where(ReportDocumentRecord.status == status)
        query = (
            query.order_by(
                ReportDocumentRecord.created_at.desc(),
                ReportDocumentRecord.id,
            )
            .limit(limit)
            .offset(offset)
        )
        async with self._database.session_factory() as session:
            result = await session.execute(query)
            return result.scalars().all()

    async def count_for_case(
        self,
        case_id: str,
        *,
        status: str | None = None,
    ) -> int:
        conditions = [ReportDocumentRecord.case_id == case_id]
        if status:
            conditions.append(ReportDocumentRecord.status == status)
        async with self._database.session_factory() as session:
            value = await session.scalar(
                select(func.count(ReportDocumentRecord.id)).where(*conditions)
            )
            return int(value or 0)

    async def list_global(
        self, *, status: str | None = None, limit: int = 100
    ) -> Sequence[ReportDocumentRecord]:
        query = select(ReportDocumentRecord)
        if status:
            query = query.where(ReportDocumentRecord.status == status)
        query = query.order_by(ReportDocumentRecord.updated_at.desc()).limit(limit)
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def latest_for_artifact(
        self, artifact_id: str
    ) -> ReportDocumentRecord | None:
        """同一 artifact 首次 import 幂等：返回已存在的最近 document。"""
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(ReportDocumentRecord)
                .where(ReportDocumentRecord.source_artifact_id == artifact_id)
                .order_by(ReportDocumentRecord.created_at.desc())
                .limit(1)
            )
            return result.scalars().first()

    async def update_draft(
        self,
        report_id: str,
        *,
        expected_lock_version: int,
        title: str | None = None,
        content_json: dict[str, Any] | None = None,
    ) -> ReportDocumentRecord | None:
        """乐观锁更新 draft（in_review 编辑由 service 先回 draft）。"""
        values: dict[str, Any] = {"lock_version": expected_lock_version + 1}
        if title is not None:
            values["title"] = title
        if content_json is not None:
            values["content_json"] = content_json
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(ReportDocumentRecord)
                .where(
                    ReportDocumentRecord.id == report_id,
                    ReportDocumentRecord.lock_version == expected_lock_version,
                    ReportDocumentRecord.status.in_(("draft", "in_review")),
                )
                .values(**values)
            )
            await session.commit()
            if int(result.rowcount or 0) != 1:
                await session.rollback()
                return None
            return await session.get(ReportDocumentRecord, report_id)

    async def change_status(
        self,
        report_id: str,
        *,
        expected_lock_version: int,
        status: str,
        published_at: Any = None,
    ) -> ReportDocumentRecord | None:
        """状态转移（乐观锁；转移合法性由 service 校验）。"""
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(ReportDocumentRecord)
                .where(
                    ReportDocumentRecord.id == report_id,
                    ReportDocumentRecord.lock_version == expected_lock_version,
                )
                .values(status=status, published_at=published_at)
            )
            await session.commit()
            if int(result.rowcount or 0) != 1:
                await session.rollback()
                return None
            return await session.get(ReportDocumentRecord, report_id)
