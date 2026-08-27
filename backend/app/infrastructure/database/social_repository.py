from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select

from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    PlatformCapabilityRecord,
    RawSocialRecord,
    SourceCommentRecord,
    SourcePostRecord,
)


@dataclass(frozen=True, slots=True)
class PersistSocialResult:
    posts_created: int = 0
    posts_updated: int = 0
    comments_created: int = 0
    comments_updated: int = 0
    raw_records_created: int = 0


class SocialRepository:
    """Persist immutable raw records and idempotent normalized social entities."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def persist_batch(
        self,
        *,
        case_id: str,
        posts: list[dict[str, object]],
    ) -> PersistSocialResult:
        counters = {
            "posts_created": 0,
            "posts_updated": 0,
            "comments_created": 0,
            "comments_updated": 0,
            "raw_records_created": 0,
        }
        async with self._database.session_factory() as session:
            for post in posts:
                platform = str(post.get("platform") or "")
                native_id = str(post.get("native_id") or post.get("id") or "")
                raw = self._dict_value(post.get("raw"))
                if await self._add_raw(
                    session,
                    case_id=case_id,
                    platform=platform,
                    record_type="post",
                    native_id=native_id,
                    payload=raw,
                ):
                    counters["raw_records_created"] += 1

                normalized = await session.scalar(
                    select(SourcePostRecord).where(
                        SourcePostRecord.case_id == case_id,
                        SourcePostRecord.platform == platform,
                        SourcePostRecord.native_id == native_id,
                    )
                )
                created = normalized is None
                if normalized is None:
                    normalized = SourcePostRecord(
                        case_id=case_id,
                        platform=platform,
                        native_id=native_id,
                        content="",
                        content_hash="",
                    )
                    session.add(normalized)

                content = str(post.get("content") or "")
                metrics = self._dict_value(post.get("metrics"))
                metrics["total"] = self._int_value(post.get("engagement"))
                normalized.content_type = str(post.get("content_type") or "post")
                normalized.title = str(post.get("title") or "")
                normalized.content = content
                normalized.author_id = str(
                    raw.get("creator_hash")
                    or raw.get("user_id")
                    or raw.get("uid")
                    or ""
                )
                normalized.author_name = str(post.get("author") or "")
                normalized.source_url = str(post.get("url") or "")
                normalized.published_at = self._parse_datetime(
                    post.get("published_at")
                )
                normalized.engagement = metrics
                normalized.raw_payload = raw
                normalized.content_hash = self._checksum_text(content)
                await session.flush()
                counters["posts_created" if created else "posts_updated"] += 1

                comments = post.get("comments")
                if not isinstance(comments, list):
                    continue
                for item in comments:
                    if not isinstance(item, dict):
                        continue
                    comment_native_id = str(
                        item.get("native_id") or item.get("comment_id") or ""
                    )
                    if not comment_native_id:
                        continue
                    comment_raw = self._dict_value(item.get("raw")) or item
                    if await self._add_raw(
                        session,
                        case_id=case_id,
                        platform=platform,
                        record_type="comment",
                        native_id=comment_native_id,
                        payload=comment_raw,
                    ):
                        counters["raw_records_created"] += 1
                    comment = await session.scalar(
                        select(SourceCommentRecord).where(
                            SourceCommentRecord.post_id == normalized.id,
                            SourceCommentRecord.platform == platform,
                            SourceCommentRecord.native_id == comment_native_id,
                        )
                    )
                    comment_created = comment is None
                    if comment is None:
                        comment = SourceCommentRecord(
                            post_id=normalized.id,
                            platform=platform,
                            native_id=comment_native_id,
                            content="",
                        )
                        session.add(comment)
                    comment.parent_native_id = (
                        str(item["parent_native_id"])
                        if item.get("parent_native_id")
                        else None
                    )
                    comment.content = str(item.get("content") or "")
                    comment.author_id = str(item.get("author_id") or "")
                    comment.author_name = str(item.get("author_name") or "")
                    comment.published_at = self._parse_datetime(
                        item.get("published_at")
                    )
                    comment.metrics = self._dict_value(item.get("metrics"))
                    comment.raw_payload = comment_raw
                    counters[
                        "comments_created" if comment_created else "comments_updated"
                    ] += 1
            await session.commit()
        return PersistSocialResult(**counters)

    async def list_posts_by_case(
        self,
        case_id: str,
    ) -> Sequence[SourcePostRecord]:
        """Return every normalized post of a case, oldest first."""
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(SourcePostRecord)
                .where(SourcePostRecord.case_id == case_id)
                .order_by(SourcePostRecord.published_at.asc())
            )
            return result.all()

    async def find_related_posts(
        self,
        case_id: str,
        query: str,
        *,
        limit: int = 20,
    ) -> Sequence[SourcePostRecord]:
        """Keyword match over post content/title, newest first.

        Used by claim evidence matching when embedding search is not
        available. Up to five keywords are ANDed; empty queries return [].
        """
        keywords = [part for part in query.split() if part][:5]
        if not keywords:
            return []
        conditions = [
            or_(
                SourcePostRecord.content.ilike(f"%{keyword}%"),
                SourcePostRecord.title.ilike(f"%{keyword}%"),
            )
            for keyword in keywords
        ]
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(SourcePostRecord)
                .where(SourcePostRecord.case_id == case_id, *conditions)
                .order_by(SourcePostRecord.published_at.desc())
                .limit(limit)
            )
            return result.all()

    async def list_platform_capabilities(
        self,
    ) -> Sequence[PlatformCapabilityRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(PlatformCapabilityRecord).order_by(
                    PlatformCapabilityRecord.platform
                )
            )
            return result.all()

    async def set_platform_capability(
        self,
        platform: str,
        *,
        status: str,
        checks: dict[str, object],
        last_error: str | None = None,
    ) -> PlatformCapabilityRecord:
        async with self._database.session_factory() as session:
            record = await session.get(PlatformCapabilityRecord, platform)
            if record is None:
                record = PlatformCapabilityRecord(platform=platform)
                session.add(record)
            record.status = status
            record.checks = checks
            record.last_error = last_error
            record.verified_at = datetime.now(UTC) if status == "ready" else None
            await session.commit()
            await session.refresh(record)
            return record

    async def _add_raw(
        self,
        session: Any,
        *,
        case_id: str,
        platform: str,
        record_type: str,
        native_id: str,
        payload: dict[str, object],
    ) -> bool:
        checksum = self._checksum_json(payload)
        existing = await session.scalar(
            select(RawSocialRecord.id).where(
                RawSocialRecord.case_id == case_id,
                RawSocialRecord.platform == platform,
                RawSocialRecord.record_type == record_type,
                RawSocialRecord.native_id == native_id,
                RawSocialRecord.checksum == checksum,
            )
        )
        if existing is not None:
            return False
        session.add(
            RawSocialRecord(
                case_id=case_id,
                platform=platform,
                record_type=record_type,
                native_id=native_id,
                payload=payload,
                checksum=checksum,
            )
        )
        return True

    @staticmethod
    def _dict_value(value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return {str(key): item for key, item in value.items()}
        return {}

    @staticmethod
    def _int_value(value: object) -> int:
        try:
            return int(str(value or 0))
        except ValueError:
            return 0

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, UTC)
        text = str(value).strip()
        if text.isdigit():
            return SocialRepository._parse_datetime(int(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=parsed.tzinfo or UTC)

    @staticmethod
    def _checksum_text(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _checksum_json(payload: dict[str, object]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
