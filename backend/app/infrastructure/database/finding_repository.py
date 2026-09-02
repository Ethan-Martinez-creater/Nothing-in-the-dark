"""M4: Finding persistence (findings + evidence/source links)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select

from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    FindingEvidenceLinkRecord,
    FindingRecord,
    FindingSourceLinkRecord,
)


class FindingRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    # ---------------- findings ----------------

    async def create(self, record: FindingRecord) -> FindingRecord:
        try:
            async with self._database.session_factory() as session:
                session.add(record)
                await session.commit()
                await session.refresh(record)
        except Exception as exc:  # noqa: BLE001
            if "uq_finding_source" in str(exc).lower() or "unique" in str(exc).lower():
                # 并发 sync 的幂等保护：同一来源已存在则不视为错误
                return record
            raise
        return record

    async def create_with_links(
        self,
        record: FindingRecord,
        *,
        source_link: tuple[str, str, str] | None = None,
        evidence_links: list[tuple[str, str]] | None = None,
    ) -> FindingRecord:
        """FC2: atomic manual-create path.

        Finding + optional source link + evidence links are written in ONE
        session with a single commit; any failure rolls everything back so a
        rejected request can never leave a partial Finding behind.
        """
        async with self._database.session_factory() as session:
            session.add(record)
            await session.flush()  # obtain the finding id, no commit yet
            if source_link is not None:
                source_type, source_id, source_path = source_link
                session.add(
                    FindingSourceLinkRecord(
                        finding_id=record.id,
                        source_type=source_type,
                        source_id=source_id,
                        source_path=source_path,
                    )
                )
            for evidence_ref, relation in evidence_links or []:
                session.add(
                    FindingEvidenceLinkRecord(
                        finding_id=record.id,
                        evidence_ref=evidence_ref,
                        relation=relation,
                    )
                )
            await session.commit()
            await session.refresh(record)
        return record

    async def get(self, finding_id: str) -> FindingRecord | None:
        async with self._database.session_factory() as session:
            return await session.get(FindingRecord, finding_id)

    async def list(
        self,
        case_id: str,
        *,
        finding_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[FindingRecord]:
        query_builder = select(FindingRecord).where(
            FindingRecord.case_id == case_id
        )
        if finding_id:
            query_builder = query_builder.where(FindingRecord.id == finding_id)
        if kind:
            query_builder = query_builder.where(FindingRecord.kind == kind)
        if status:
            query_builder = query_builder.where(FindingRecord.status == status)
        if query:
            like = f"%{query}%"
            query_builder = query_builder.where(
                or_(
                    FindingRecord.title.ilike(like),
                    FindingRecord.statement.ilike(like),
                )
            )
        query_builder = (
            query_builder.order_by(
                FindingRecord.updated_at.desc(), FindingRecord.id
            )
            .limit(limit)
            .offset(offset)
        )
        async with self._database.session_factory() as session:
            return (await session.scalars(query_builder)).all()

    async def count(
        self,
        case_id: str,
        *,
        kind: str | None = None,
        status: str | None = None,
        query: str | None = None,
    ) -> int:
        conditions = [FindingRecord.case_id == case_id]
        if kind:
            conditions.append(FindingRecord.kind == kind)
        if status:
            conditions.append(FindingRecord.status == status)
        if query:
            like = f"%{query}%"
            conditions.append(
                or_(
                    FindingRecord.title.ilike(like),
                    FindingRecord.statement.ilike(like),
                )
            )
        async with self._database.session_factory() as session:
            value = await session.scalar(
                select(func.count(FindingRecord.id)).where(*conditions)
            )
            return int(value or 0)

    async def update_status(
        self, finding_id: str, status: str
    ) -> FindingRecord | None:
        async with self._database.session_factory() as session:
            record = await session.get(FindingRecord, finding_id)
            if record is None:
                return None
            record.status = status
            await session.commit()
            await session.refresh(record)
            return record

    # ---------------- evidence links ----------------

    async def add_evidence_link(
        self, finding_id: str, evidence_ref: str, relation: str
    ) -> FindingEvidenceLinkRecord:
        link = FindingEvidenceLinkRecord(
            finding_id=finding_id,
            evidence_ref=evidence_ref,
            relation=relation,
        )
        async with self._database.session_factory() as session:
            session.add(link)
            await session.commit()
            await session.refresh(link)
        return link

    async def remove_evidence_link(
        self, finding_id: str, evidence_ref: str, relation: str
    ) -> bool:
        async with self._database.session_factory() as session:
            link = (
                await session.scalars(
                    select(FindingEvidenceLinkRecord).where(
                        FindingEvidenceLinkRecord.finding_id == finding_id,
                        FindingEvidenceLinkRecord.evidence_ref == evidence_ref,
                        FindingEvidenceLinkRecord.relation == relation,
                    )
                )
            ).first()
            if link is None:
                return False
            await session.delete(link)
            await session.commit()
            return True

    async def list_evidence_links(
        self, finding_id: str
    ) -> Sequence[FindingEvidenceLinkRecord]:
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(FindingEvidenceLinkRecord).where(
                    FindingEvidenceLinkRecord.finding_id == finding_id
                )
            )
            return result.scalars().all()

    # ---------------- source links（幂等键） ----------------

    async def get_source_link(
        self, source_type: str, source_id: str, source_path: str
    ) -> FindingSourceLinkRecord | None:
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(FindingSourceLinkRecord).where(
                    FindingSourceLinkRecord.source_type == source_type,
                    FindingSourceLinkRecord.source_id == source_id,
                    FindingSourceLinkRecord.source_path == source_path,
                )
            )
            return result.scalars().first()

    async def create_source_link(
        self,
        finding_id: str,
        source_type: str,
        source_id: str,
        source_path: str,
    ) -> FindingSourceLinkRecord | None:
        """幂等创建：唯一键已存在时返回 None（不重置已 Review 的 Finding）。"""
        existing = await self.get_source_link(source_type, source_id, source_path)
        if existing is not None:
            return None
        link = FindingSourceLinkRecord(
            finding_id=finding_id,
            source_type=source_type,
            source_id=source_id,
            source_path=source_path,
        )
        async with self._database.session_factory() as session:
            session.add(link)
            await session.commit()
            await session.refresh(link)
        return link

    async def list_source_links(
        self, finding_id: str
    ) -> Sequence[FindingSourceLinkRecord]:
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(FindingSourceLinkRecord).where(
                    FindingSourceLinkRecord.finding_id == finding_id
                )
            )
            return result.scalars().all()

    # ---------------- 事务内状态更新（Review 集成使用） ----------------

    async def update_status_in_session(
        self, session: Any, finding: FindingRecord, status: str
    ) -> None:
        """在调用方事务内更新 Finding 状态（Review decide 同一 commit）。"""
        finding.status = status
        session.add(finding)
