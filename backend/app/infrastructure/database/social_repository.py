from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import and_, func, or_, select

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

    async def list_posts_page(
        self,
        case_id: str,
        *,
        platform: str | None = None,
        platforms: list[str] | None = None,
        q: str | None = None,
        author: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        sort_order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[SourcePostRecord]:
        """分页 raw posts（platform(s)/关键词/作者/时间范围过滤，确定性排序）。

        ``platform`` 为向后兼容的单平台参数，``platforms`` 为多选；
        两者合并取并集，Service 层负责归一化到 ``platforms``。
        """
        effective_platforms = list(platforms or [])
        if platform:
            effective_platforms.append(platform)
        conditions = self._post_filters(
            case_id,
            platforms=effective_platforms or None,
            q=q,
            author=author,
            date_from=date_from,
            date_to=date_to,
        )
        order = (
            SourcePostRecord.published_at.desc()
            if sort_order == "newest"
            else SourcePostRecord.published_at.asc()
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(SourcePostRecord)
                .where(*conditions)
                .order_by(order, SourcePostRecord.id)
                .limit(limit)
                .offset(offset)
            )
            return result.all()

    async def list_post_time_rows(
        self,
        case_id: str,
        *,
        platforms: list[str] | None = None,
        q: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[tuple[datetime | None, str]]:
        """C8.2: 轻量聚合原始行 —— (published_at, platform)，Python 侧按
        天/平台聚合（双方言安全：不依赖 SQL date 函数）。"""
        conditions = self._post_filters(
            case_id,
            platforms=platforms,
            q=q,
            date_from=date_from,
            date_to=date_to,
        )
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(SourcePostRecord.published_at, SourcePostRecord.platform)
                .where(*conditions)
                .order_by(SourcePostRecord.published_at.asc(), SourcePostRecord.id)
            )
            return [(row[0], row[1]) for row in rows.all()]

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

    @staticmethod
    def _post_filters(
        case_id: str,
        *,
        platforms: list[str] | None = None,
        q: str | None = None,
        author: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[Any]:
        """Post 查询共享过滤条件（typed bind parameters，无 SQL 拼接）。"""
        conditions = [SourcePostRecord.case_id == case_id]
        if platforms:
            conditions.append(SourcePostRecord.platform.in_(platforms))
        if q:
            conditions.append(
                or_(
                    SourcePostRecord.content.ilike(f"%{q}%"),
                    SourcePostRecord.title.ilike(f"%{q}%"),
                )
            )
        if author:
            conditions.append(
                or_(
                    SourcePostRecord.author_name.ilike(f"%{author}%"),
                    SourcePostRecord.author_id.ilike(f"%{author}%"),
                )
            )
        if date_from is not None:
            conditions.append(SourcePostRecord.published_at >= date_from)
        if date_to is not None:
            conditions.append(SourcePostRecord.published_at <= date_to)
        return conditions

    @staticmethod
    def _comment_filters(
        case_id: str,
        *,
        post_id: str | None = None,
        platforms: list[str] | None = None,
        q: str | None = None,
        author: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[Any]:
        """Comment 查询共享过滤条件。Comment 无独立 case 边界，必须 JOIN
        SourcePost 并以 SourcePost.case_id 作为 Case scope（DB-INV-3）。"""
        conditions = [SourcePostRecord.case_id == case_id]
        if post_id is not None:
            conditions.append(SourceCommentRecord.post_id == post_id)
        if platforms:
            conditions.append(SourcePostRecord.platform.in_(platforms))
        if q:
            conditions.append(SourceCommentRecord.content.ilike(f"%{q}%"))
        if author:
            conditions.append(
                or_(
                    SourceCommentRecord.author_name.ilike(f"%{author}%"),
                    SourceCommentRecord.author_id.ilike(f"%{author}%"),
                )
            )
        if date_from is not None:
            conditions.append(SourceCommentRecord.published_at >= date_from)
        if date_to is not None:
            conditions.append(SourceCommentRecord.published_at <= date_to)
        return conditions

    async def count_posts(
        self,
        case_id: str,
        *,
        platforms: list[str] | None = None,
        q: str | None = None,
        author: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> int:
        """Exact count of persisted posts matching the filters (current DB)."""
        conditions = self._post_filters(
            case_id,
            platforms=platforms,
            q=q,
            author=author,
            date_from=date_from,
            date_to=date_to,
        )
        async with self._database.session_factory() as session:
            value = await session.scalar(
                select(func.count(SourcePostRecord.id)).where(*conditions)
            )
            return int(value or 0)

    async def get_post_for_case(
        self,
        case_id: str,
        *,
        post_id: str | None = None,
        platform: str | None = None,
        native_id: str | None = None,
    ) -> SourcePostRecord | None:
        """Exact post lookup strictly scoped to the case (DB-INV-4):
        a post belonging to another case must resolve to None."""
        if post_id is not None:
            async with self._database.session_factory() as session:
                return await session.scalar(
                    select(SourcePostRecord).where(
                        SourcePostRecord.id == post_id,
                        SourcePostRecord.case_id == case_id,
                    )
                )
        if platform and native_id:
            async with self._database.session_factory() as session:
                return await session.scalar(
                    select(SourcePostRecord).where(
                        SourcePostRecord.case_id == case_id,
                        SourcePostRecord.platform == platform,
                        SourcePostRecord.native_id == native_id,
                    )
                )
        return None

    async def list_comments_page(
        self,
        case_id: str,
        *,
        post_id: str | None = None,
        platforms: list[str] | None = None,
        q: str | None = None,
        author: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        sort_order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[SourceCommentRecord]:
        """Paginated comments, always JOINed through SourcePost for case scope."""
        conditions = self._comment_filters(
            case_id,
            post_id=post_id,
            platforms=platforms,
            q=q,
            author=author,
            date_from=date_from,
            date_to=date_to,
        )
        order = (
            SourceCommentRecord.published_at.desc()
            if sort_order == "newest"
            else SourceCommentRecord.published_at.asc()
        )
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(SourceCommentRecord)
                .join(
                    SourcePostRecord,
                    SourceCommentRecord.post_id == SourcePostRecord.id,
                )
                .where(*conditions)
                .order_by(order, SourceCommentRecord.id)
                .limit(limit)
                .offset(offset)
            )
            return result.all()

    async def count_comments(
        self,
        case_id: str,
        *,
        post_id: str | None = None,
        platforms: list[str] | None = None,
        q: str | None = None,
        author: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> int:
        """Exact comment count scoped through SourcePost.case_id."""
        conditions = self._comment_filters(
            case_id,
            post_id=post_id,
            platforms=platforms,
            q=q,
            author=author,
            date_from=date_from,
            date_to=date_to,
        )
        async with self._database.session_factory() as session:
            value = await session.scalar(
                select(func.count(SourceCommentRecord.id))
                .join(
                    SourcePostRecord,
                    SourceCommentRecord.post_id == SourcePostRecord.id,
                )
                .where(*conditions)
            )
            return int(value or 0)

    async def count_posts_by_platform(
        self,
        case_id: str,
        *,
        platforms: list[str] | None = None,
        q: str | None = None,
        author: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[tuple[str, int]]:
        conditions = self._post_filters(
            case_id,
            platforms=platforms,
            q=q,
            author=author,
            date_from=date_from,
            date_to=date_to,
        )
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(SourcePostRecord.platform, func.count(SourcePostRecord.id))
                .where(*conditions)
                .group_by(SourcePostRecord.platform)
            )
            return [(str(row[0]), int(row[1])) for row in rows.all()]

    async def count_posts_by_content_type(
        self,
        case_id: str,
        *,
        platforms: list[str] | None = None,
        q: str | None = None,
        author: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[tuple[str, int]]:
        conditions = self._post_filters(
            case_id,
            platforms=platforms,
            q=q,
            author=author,
            date_from=date_from,
            date_to=date_to,
        )
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(
                    SourcePostRecord.content_type, func.count(SourcePostRecord.id)
                )
                .where(*conditions)
                .group_by(SourcePostRecord.content_type)
            )
            return [(str(row[0]), int(row[1])) for row in rows.all()]

    async def latest_post_created_at(self, case_id: str) -> datetime | None:
        """V3 §22 Quality fingerprint 输入：posts 最新持久化时间。"""
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(func.max(SourcePostRecord.created_at)).where(
                    SourcePostRecord.case_id == case_id
                )
            )

    async def list_case_post_authors(
        self, case_id: str
    ) -> list[tuple[str, str, str]]:
        """V3 §28: Case 内出现过的作者账号（platform, native_id, name）。

        AccountRecord 是全局表（case_id 只记首次观察），因此 Case 的账号
        appearance 必须补充 SourcePost.author 维度；一次分组查询，禁止 N+1。
        """
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(
                    SourcePostRecord.platform,
                    SourcePostRecord.author_id,
                    func.max(SourcePostRecord.author_name),
                )
                .where(
                    SourcePostRecord.case_id == case_id,
                    SourcePostRecord.author_id != "",
                )
                .group_by(SourcePostRecord.platform, SourcePostRecord.author_id)
            )
            return [
                (str(platform), str(author_id), str(name or ""))
                for platform, author_id, name in rows.all()
            ]

    # ---------------- V3 §37/§39: cross-case batch matching ----------------

    async def find_cross_case_native_post_matches(
        self,
        case_id: str,
        platform_native_pairs: Sequence[tuple[str, str]],
        limit: int = 2000,
    ) -> Sequence[SourcePostRecord]:
        """§37 shared_post：同 platform + native_id、不同 case 的原始帖。

        一次批量 IN/OR 查询（禁逐 Post N+1）；结果排除 anchor case。
        """
        if not platform_native_pairs:
            return []
        by_platform: dict[str, list[str]] = {}
        for platform, native_id in platform_native_pairs:
            by_platform.setdefault(platform, []).append(native_id)
        conditions = [
            and_(
                SourcePostRecord.platform == platform,
                SourcePostRecord.native_id.in_(tuple(native_ids)),
            )
            for platform, native_ids in by_platform.items()
        ]
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(SourcePostRecord)
                .where(
                    SourcePostRecord.case_id != case_id,
                    or_(*conditions),
                )
                .order_by(SourcePostRecord.case_id, SourcePostRecord.id)
                .limit(limit)
            )
            return result.all()

    async def find_cross_case_content_hash_matches(
        self,
        case_id: str,
        hashes: Sequence[str],
        limit: int = 2000,
    ) -> Sequence[SourcePostRecord]:
        """§39 shared_content：同 raw content_hash、不同 case 的帖子。

        走 (content_hash, case_id) 复合索引；调用方需保证 hash 非空且
        已剔除与 shared_post 重叠的原始 Post。
        """
        unique_hashes = sorted({h for h in hashes if h})
        if not unique_hashes:
            return []
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(SourcePostRecord)
                .where(
                    SourcePostRecord.case_id != case_id,
                    SourcePostRecord.content_hash.in_(tuple(unique_hashes)),
                )
                .order_by(SourcePostRecord.case_id, SourcePostRecord.id)
                .limit(limit)
            )
            return result.all()

    async def list_case_post_content_hashes(
        self, case_id: str, limit: int = 20000
    ) -> list[tuple[str, str]]:
        """anchor case 的 (post_id, content_hash)（detector expected set 输入）。"""
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(SourcePostRecord.id, SourcePostRecord.content_hash)
                .where(
                    SourcePostRecord.case_id == case_id,
                    SourcePostRecord.content_hash != "",
                )
                .limit(limit)
            )
            return [(str(post_id), str(content_hash)) for post_id, content_hash in rows.all()]

    async def list_case_native_pairs(
        self, case_id: str, limit: int = 20000
    ) -> list[tuple[str, str, str]]:
        """anchor case 的 (post_id, platform, native_id)（shared_post detector 输入）。"""
        async with self._database.session_factory() as session:
            rows = await session.execute(
                select(
                    SourcePostRecord.id,
                    SourcePostRecord.platform,
                    SourcePostRecord.native_id,
                )
                .where(SourcePostRecord.case_id == case_id)
                .limit(limit)
            )
            return [
                (str(post_id), str(platform), str(native_id))
                for post_id, platform, native_id in rows.all()
            ]

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
