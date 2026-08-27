"""Cross-platform alignment persistence (06)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ResourceNotFoundError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    AlignmentCandidateRecord,
    CanonicalEntityRecord,
    ContentFamilyMemberRecord,
    ContentFamilyRecord,
    EntityMentionRecord,
    NarrativeMembershipRecord,
)
from app.services.alignment import undirected_key


def _now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


class AlignmentRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    # ---- canonical entities ---------------------------------------------

    async def upsert_canonical_entity(
        self,
        *,
        case_id: str,
        entity_type: str,
        canonical_name: str,
        aliases: list[str] | None = None,
        description: str = "",
        created_by: str = "system",
    ) -> CanonicalEntityRecord:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(CanonicalEntityRecord).where(
                    CanonicalEntityRecord.case_id == case_id,
                    CanonicalEntityRecord.entity_type == entity_type,
                    CanonicalEntityRecord.canonical_name == canonical_name,
                )
            )
            if record is not None:
                return record
            record = CanonicalEntityRecord(
                case_id=case_id,
                entity_type=entity_type,
                canonical_name=canonical_name,
                aliases=aliases or [],
                description=description,
                created_by=created_by,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_entity(self, entity_id: str) -> CanonicalEntityRecord:
        async with self._database.session_factory() as session:
            record = await session.get(CanonicalEntityRecord, entity_id)
            if record is None:
                raise ResourceNotFoundError("canonical entity", entity_id)
            return record

    async def list_entities(
        self,
        case_id: str,
        *,
        entity_type: str | None = None,
    ) -> Sequence[CanonicalEntityRecord]:
        query = select(CanonicalEntityRecord).where(CanonicalEntityRecord.case_id == case_id)
        if entity_type is not None:
            query = query.where(CanonicalEntityRecord.entity_type == entity_type)
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def create_entity_mention(
        self,
        *,
        case_id: str,
        entity_id: str,
        platform_object_type: str,
        platform_object_id: str,
        text_span: dict[str, object] | None = None,
        confidence: float = 0,
        method: str = "",
    ) -> EntityMentionRecord:
        record = EntityMentionRecord(
            case_id=case_id,
            entity_id=entity_id,
            platform_object_type=platform_object_type,
            platform_object_id=platform_object_id,
            text_span=text_span or {},
            confidence=confidence,
            method=method,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(EntityMentionRecord).where(
                        EntityMentionRecord.entity_id == entity_id,
                        EntityMentionRecord.platform_object_type == platform_object_type,
                        EntityMentionRecord.platform_object_id == platform_object_id,
                    )
                )
                assert existing is not None
                return existing
            await session.refresh(record)
        return record

    # ---- alignment candidates -------------------------------------------

    async def create_alignment_candidate(
        self,
        *,
        case_id: str,
        left_type: str,
        left_id: str,
        right_type: str,
        right_id: str,
        relation_type: str = "same_as",
        feature_scores: dict[str, object] | None = None,
        combined_score: float = 0,
        decision: str = "pending",
        model_version: str = "1.0.0",
    ) -> AlignmentCandidateRecord | None:
        left_key, right_key = undirected_key(left_type, left_id, right_type, right_id)
        record = AlignmentCandidateRecord(
            case_id=case_id,
            left_type=left_type,
            left_id=left_id,
            right_type=right_type,
            right_id=right_id,
            left_key=left_key,
            right_key=right_key,
            relation_type=relation_type,
            feature_scores=feature_scores or {},
            combined_score=combined_score,
            decision=decision,
            model_version=model_version,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            await session.refresh(record)
        return record

    async def get_candidate(self, candidate_id: str) -> AlignmentCandidateRecord:
        async with self._database.session_factory() as session:
            record = await session.get(AlignmentCandidateRecord, candidate_id)
            if record is None:
                raise ResourceNotFoundError("alignment candidate", candidate_id)
            return record

    async def list_candidates(
        self,
        case_id: str,
        *,
        decision: str | None = None,
        relation_type: str | None = None,
        limit: int = 200,
    ) -> Sequence[AlignmentCandidateRecord]:
        query = select(AlignmentCandidateRecord).where(AlignmentCandidateRecord.case_id == case_id)
        if decision is not None:
            query = query.where(AlignmentCandidateRecord.decision == decision)
        if relation_type is not None:
            query = query.where(AlignmentCandidateRecord.relation_type == relation_type)
        query = query.order_by(AlignmentCandidateRecord.combined_score.desc()).limit(limit)
        async with self._database.session_factory() as session:
            return (await session.scalars(query)).all()

    async def set_candidate_decision(
        self,
        candidate_id: str,
        decision: str,
        *,
        review_id: str | None = None,
    ) -> AlignmentCandidateRecord:
        async with self._database.session_factory() as session:
            record = await session.get(AlignmentCandidateRecord, candidate_id)
            if record is None:
                raise ResourceNotFoundError("alignment candidate", candidate_id)
            record.decision = decision
            if review_id is not None:
                record.review_id = review_id
            record.updated_at = _now()
            await session.commit()
            await session.refresh(record)
        return record

    # ---- content families ------------------------------------------------

    async def create_content_family(
        self,
        *,
        case_id: str,
        label: str = "",
        earliest_known_id: str | None = None,
        summary: str = "",
    ) -> ContentFamilyRecord:
        record = ContentFamilyRecord(
            case_id=case_id,
            label=label,
            earliest_known_id=earliest_known_id,
            summary=summary,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_family(self, family_id: str) -> ContentFamilyRecord:
        async with self._database.session_factory() as session:
            record = await session.get(ContentFamilyRecord, family_id)
            if record is None:
                raise ResourceNotFoundError("content family", family_id)
            return record

    async def list_families(self, case_id: str) -> Sequence[ContentFamilyRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(ContentFamilyRecord).where(ContentFamilyRecord.case_id == case_id)
                )
            ).all()

    async def add_family_member(
        self,
        *,
        family_id: str,
        member_type: str = "post",
        member_id: str = "",
        relation: str = "original",
        time_offset_ms: int | None = None,
        edit_features: dict[str, object] | None = None,
        decision_source: str = "algorithm",
    ) -> ContentFamilyMemberRecord | None:
        record = ContentFamilyMemberRecord(
            family_id=family_id,
            member_type=member_type,
            member_id=member_id,
            relation=relation,
            time_offset_ms=time_offset_ms,
            edit_features=edit_features or {},
            decision_source=decision_source,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            await session.refresh(record)
        return record

    async def list_family_members(
        self,
        family_id: str,
    ) -> Sequence[ContentFamilyMemberRecord]:
        async with self._database.session_factory() as session:
            return (
                await session.scalars(
                    select(ContentFamilyMemberRecord).where(
                        ContentFamilyMemberRecord.family_id == family_id
                    )
                )
            ).all()

    # ---- narrative memberships ------------------------------------------

    async def create_narrative_membership(
        self,
        *,
        case_id: str,
        narrative_id: str,
        post_id: str | None = None,
        claim_id: str | None = None,
        membership_score: float = 0,
    ) -> NarrativeMembershipRecord | None:
        record = NarrativeMembershipRecord(
            case_id=case_id,
            narrative_id=narrative_id,
            post_id=post_id,
            claim_id=claim_id,
            membership_score=membership_score,
        )
        async with self._database.session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            await session.refresh(record)
        return record

    async def find_entity_for_object(
        self,
        case_id: str,
        object_type: str,
        object_id: str,
    ) -> CanonicalEntityRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(CanonicalEntityRecord)
                .join(
                    EntityMentionRecord, EntityMentionRecord.entity_id == CanonicalEntityRecord.id
                )
                .where(
                    CanonicalEntityRecord.case_id == case_id,
                    EntityMentionRecord.platform_object_type == object_type,
                    EntityMentionRecord.platform_object_id == object_id,
                )
            )

    async def mark_entity_confirmed(self, entity_id: str) -> None:
        async with self._database.session_factory() as session:
            await session.execute(
                update(CanonicalEntityRecord)
                .where(CanonicalEntityRecord.id == entity_id)
                .values(status="confirmed")
            )
            await session.commit()

    async def merge_entities(self, target_id: str, source_id: str) -> None:
        if target_id == source_id:
            return
        async with self._database.session_factory() as session:
            mentions = (
                await session.scalars(
                    select(EntityMentionRecord).where(EntityMentionRecord.entity_id == source_id)
                )
            ).all()
            for mention in mentions:
                existing = await session.scalar(
                    select(EntityMentionRecord).where(
                        EntityMentionRecord.entity_id == target_id,
                        EntityMentionRecord.platform_object_type == mention.platform_object_type,
                        EntityMentionRecord.platform_object_id == mention.platform_object_id,
                    )
                )
                if existing is None:
                    mention.entity_id = target_id
                else:
                    await session.delete(mention)
            source = await session.get(CanonicalEntityRecord, source_id)
            if source is not None:
                source.status = "merged"
            await session.commit()

    async def find_family_for_member(
        self,
        case_id: str,
        member_type: str,
        member_id: str,
    ) -> ContentFamilyRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(ContentFamilyRecord)
                .join(
                    ContentFamilyMemberRecord,
                    ContentFamilyMemberRecord.family_id == ContentFamilyRecord.id,
                )
                .where(
                    ContentFamilyRecord.case_id == case_id,
                    ContentFamilyRecord.status != "merged",
                    ContentFamilyMemberRecord.member_type == member_type,
                    ContentFamilyMemberRecord.member_id == member_id,
                )
            )

    async def merge_families(self, target_id: str, source_id: str) -> None:
        if target_id == source_id:
            return
        async with self._database.session_factory() as session:
            members = (
                await session.scalars(
                    select(ContentFamilyMemberRecord).where(
                        ContentFamilyMemberRecord.family_id == source_id
                    )
                )
            ).all()
            for member in members:
                existing = await session.scalar(
                    select(ContentFamilyMemberRecord).where(
                        ContentFamilyMemberRecord.family_id == target_id,
                        ContentFamilyMemberRecord.member_id == member.member_id,
                    )
                )
                if existing is None:
                    member.family_id = target_id
                else:
                    await session.delete(member)
            source = await session.get(ContentFamilyRecord, source_id)
            if source is not None:
                source.status = "merged"
            await session.commit()

    async def retract_candidate_materialization(self, decision_source: str) -> None:
        async with self._database.session_factory() as session:
            await session.execute(
                delete(ContentFamilyMemberRecord).where(
                    ContentFamilyMemberRecord.decision_source == decision_source
                )
            )
            await session.execute(
                delete(EntityMentionRecord).where(EntityMentionRecord.method == decision_source)
            )
            await session.commit()
