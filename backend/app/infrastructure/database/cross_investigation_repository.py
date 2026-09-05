"""V3 §34: Cross-Investigation Link persistence。

fingerprint = SHA256(left_case + right_case + relation_type + algorithm_version)
（§10）：一个 Case Pair + relation_type + algorithm_version 只有一条聚合
Link。每个 detector 刷新先算完整 expected set，再 upsert，最后
reconcile_for_anchor 把本次不再成立的 link 置 is_active=false（§10.1，
不物理删除）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update

from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import CrossInvestigationLinkRecord


def cross_link_fingerprint(
    *,
    left_case_id: str,
    right_case_id: str,
    relation_type: str,
    algorithm_version: str,
) -> str:
    """§10 固定 fingerprint；pair 由上层 canonical ordering 保证 left<right。"""
    payload = f"{left_case_id}{right_case_id}{relation_type}{algorithm_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CrossInvestigationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert_link(
        self,
        *,
        left_case_id: str,
        right_case_id: str,
        relation_type: str,
        status: str,
        score: float,
        evidence_count: int,
        evidence_refs: list[dict[str, Any]],
        feature_scores: dict[str, Any],
        algorithm_version: str,
        max_evidence_refs: int = 50,
    ) -> CrossInvestigationLinkRecord:
        # §10：pair canonical ordering 兜底（detector 已保证，此处防御）
        left_case_id, right_case_id = sorted((left_case_id, right_case_id))
        fingerprint = cross_link_fingerprint(
            left_case_id=left_case_id,
            right_case_id=right_case_id,
            relation_type=relation_type,
            algorithm_version=algorithm_version,
        )
        now = datetime.now(UTC)
        async with self._database.session_factory() as session:
            link = await session.scalar(
                select(CrossInvestigationLinkRecord).where(
                    CrossInvestigationLinkRecord.fingerprint == fingerprint
                )
            )
            if link is None:
                link = CrossInvestigationLinkRecord(
                    left_case_id=left_case_id,
                    right_case_id=right_case_id,
                    relation_type=relation_type,
                    status=status,
                    is_active=True,
                    score=score,
                    evidence_count=evidence_count,
                    evidence_refs_json=evidence_refs[:max_evidence_refs],
                    feature_scores_json=dict(feature_scores),
                    fingerprint=fingerprint,
                    algorithm_version=algorithm_version,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                session.add(link)
                try:
                    await session.commit()
                    await session.refresh(link)
                    return link
                except Exception:  # noqa: BLE001 - fingerprint 竞争极低频
                    await session.rollback()
                    link = await session.scalar(
                        select(CrossInvestigationLinkRecord).where(
                            CrossInvestigationLinkRecord.fingerprint
                            == fingerprint
                        )
                    )
                    if link is None:
                        raise
            link.score = score
            link.status = status
            link.is_active = True
            link.evidence_count = evidence_count
            # JSON 列整体替换，避免原地 mutate 不触发 dirty
            link.evidence_refs_json = list(evidence_refs[:max_evidence_refs])
            link.feature_scores_json = dict(feature_scores)
            link.last_seen_at = now
            await session.commit()
            await session.refresh(link)
            return link

    async def reconcile_for_anchor(
        self,
        case_id: str,
        relation_type: str,
        algorithm_version: str,
        expected_fingerprints: set[str],
    ) -> int:
        """§10.1：把 anchor case 触及、本次 expected 之外的 link 置 inactive。"""
        async with self._database.session_factory() as session:
            result = await session.execute(
                update(CrossInvestigationLinkRecord)
                .where(
                    CrossInvestigationLinkRecord.is_active.is_(True),
                    CrossInvestigationLinkRecord.relation_type == relation_type,
                    CrossInvestigationLinkRecord.algorithm_version
                    == algorithm_version,
                    or_(
                        CrossInvestigationLinkRecord.left_case_id == case_id,
                        CrossInvestigationLinkRecord.right_case_id == case_id,
                    ),
                    CrossInvestigationLinkRecord.fingerprint.not_in(
                        tuple(expected_fingerprints) or ("__none__",)
                    ),
                )
                .values(is_active=False)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def list_for_case(
        self,
        case_id: str,
        *,
        relation_type: str | None = None,
        status: str | None = None,
        active_only: bool = True,
        limit: int = 200,
    ) -> Sequence[CrossInvestigationLinkRecord]:
        query = select(CrossInvestigationLinkRecord).where(
            or_(
                CrossInvestigationLinkRecord.left_case_id == case_id,
                CrossInvestigationLinkRecord.right_case_id == case_id,
            )
        )
        if active_only:
            query = query.where(CrossInvestigationLinkRecord.is_active.is_(True))
        if relation_type:
            query = query.where(
                CrossInvestigationLinkRecord.relation_type == relation_type
            )
        if status:
            query = query.where(CrossInvestigationLinkRecord.status == status)
        query = (
            query.order_by(CrossInvestigationLinkRecord.score.desc().nullslast())
            .limit(limit)
        )
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def list_between(
        self,
        left_case_id: str,
        right_case_id: str,
        *,
        active_only: bool = True,
    ) -> Sequence[CrossInvestigationLinkRecord]:
        query = select(CrossInvestigationLinkRecord).where(
            or_(
                and_(
                    CrossInvestigationLinkRecord.left_case_id == left_case_id,
                    CrossInvestigationLinkRecord.right_case_id == right_case_id,
                ),
                and_(
                    CrossInvestigationLinkRecord.left_case_id == right_case_id,
                    CrossInvestigationLinkRecord.right_case_id == left_case_id,
                ),
            )
        )
        if active_only:
            query = query.where(CrossInvestigationLinkRecord.is_active.is_(True))
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def list_workspace(
        self,
        *,
        relation_type: str | None = None,
        status: str | None = None,
        active_only: bool = True,
        limit: int = 200,
    ) -> Sequence[CrossInvestigationLinkRecord]:
        query = select(CrossInvestigationLinkRecord)
        if active_only:
            query = query.where(CrossInvestigationLinkRecord.is_active.is_(True))
        if relation_type:
            query = query.where(
                CrossInvestigationLinkRecord.relation_type == relation_type
            )
        if status:
            query = query.where(CrossInvestigationLinkRecord.status == status)
        query = (
            query.order_by(CrossInvestigationLinkRecord.updated_at.desc())
            .limit(min(limit, 200))
        )
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def list_workspace_detector_page(
        self,
        *,
        status: str | None = None,
        active_only: bool = True,
        after_updated_at: datetime | None = None,
        after_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[CrossInvestigationLinkRecord]:
        """FC1：detector 专用 keyset 分页（UI/ browse 的 list_workspace 不变）。

        排序固定 updated_at ASC, id ASC；cursor = (updated_at, id)。
        updated_at 为 NULL 的行不进入 detector 扫描（无法作为稳定 cursor；
        正常 upsert 链路总会写入 updated_at）。
        """
        query = select(CrossInvestigationLinkRecord).where(
            CrossInvestigationLinkRecord.updated_at.isnot(None)
        )
        if active_only:
            query = query.where(CrossInvestigationLinkRecord.is_active.is_(True))
        if status:
            query = query.where(CrossInvestigationLinkRecord.status == status)
        if after_updated_at is not None:
            query = query.where(
                or_(
                    CrossInvestigationLinkRecord.updated_at > after_updated_at,
                    and_(
                        CrossInvestigationLinkRecord.updated_at
                        == after_updated_at,
                        CrossInvestigationLinkRecord.id > (after_id or ""),
                    ),
                )
            )
        query = (
            query.order_by(
                CrossInvestigationLinkRecord.updated_at.asc(),
                CrossInvestigationLinkRecord.id.asc(),
            )
            .limit(max(1, min(limit, 500)))
        )
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def count_for_case(self, case_id: str, *, active_only: bool = True) -> int:
        query = select(func.count(CrossInvestigationLinkRecord.id)).where(
            or_(
                CrossInvestigationLinkRecord.left_case_id == case_id,
                CrossInvestigationLinkRecord.right_case_id == case_id,
            )
        )
        if active_only:
            query = query.where(CrossInvestigationLinkRecord.is_active.is_(True))
        async with self._database.session_factory() as session:
            return int(await session.scalar(query) or 0)

    async def related_case_ids(self, case_id: str) -> list[str]:
        links = await self.list_for_case(case_id)
        related: set[str] = set()
        for link in links:
            if link.left_case_id == case_id:
                related.add(link.right_case_id)
            else:
                related.add(link.left_case_id)
        return sorted(related)
