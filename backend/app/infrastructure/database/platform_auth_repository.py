"""Platform auth credential persistence (Phase 2).

Repository 只负责存取加密载荷与状态元数据；加解密由 Service/Cipher 层
完成，Repository 不接触明文 cookie。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    PlatformAuthCredentialRecord,
    utc_now,
)

STATUS_ACTIVE = "active"
STATUS_INVALID = "invalid"
STATUS_REVOKED = "revoked"


class PlatformAuthRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_by_platform(
        self, platform: str
    ) -> PlatformAuthCredentialRecord | None:
        async with self._database.session_factory() as session:
            return await session.scalar(
                select(PlatformAuthCredentialRecord).where(
                    PlatformAuthCredentialRecord.platform == platform
                )
            )

    async def upsert(
        self,
        *,
        platform: str,
        nonce_b64: str,
        ciphertext_b64: str,
        account_label: str | None = None,
        status: str = STATUS_ACTIVE,
        state_format_version: int = 1,
        key_version: int = 1,
        last_validated_at: datetime | None = None,
        expires_at: datetime | None = None,
        last_error: str | None = None,
    ) -> PlatformAuthCredentialRecord:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(PlatformAuthCredentialRecord).where(
                    PlatformAuthCredentialRecord.platform == platform
                )
            )
            if record is None:
                record = PlatformAuthCredentialRecord(
                    platform=platform,
                    nonce_b64=nonce_b64,
                    ciphertext_b64=ciphertext_b64,
                    account_label=account_label,
                    status=status,
                    state_format_version=state_format_version,
                    key_version=key_version,
                    last_validated_at=last_validated_at,
                    expires_at=expires_at,
                    last_error=last_error,
                )
                session.add(record)
                try:
                    await session.commit()
                    await session.refresh(record)
                    return record
                except IntegrityError:
                    # 并发 upsert：UNIQUE(platform) 冲突时转为更新既有行。
                    await session.rollback()
                    record = await session.scalar(
                        select(PlatformAuthCredentialRecord).where(
                            PlatformAuthCredentialRecord.platform == platform
                        )
                    )
                    if record is None:
                        raise
            record.nonce_b64 = nonce_b64
            record.ciphertext_b64 = ciphertext_b64
            record.account_label = account_label
            record.status = status
            record.state_format_version = state_format_version
            record.key_version = key_version
            record.last_validated_at = last_validated_at
            record.expires_at = expires_at
            record.last_error = last_error
            await session.commit()
            await session.refresh(record)
            return record

    async def mark_validated(
        self, platform: str, *, validated_at: datetime | None = None
    ) -> PlatformAuthCredentialRecord | None:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(PlatformAuthCredentialRecord).where(
                    PlatformAuthCredentialRecord.platform == platform
                )
            )
            if record is None:
                return None
            record.status = STATUS_ACTIVE
            record.last_validated_at = validated_at or utc_now()
            record.last_error = None
            await session.commit()
            await session.refresh(record)
            return record

    async def mark_invalid(
        self,
        platform: str,
        *,
        error: str,
        validated_at: datetime | None = None,
    ) -> PlatformAuthCredentialRecord | None:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(PlatformAuthCredentialRecord).where(
                    PlatformAuthCredentialRecord.platform == platform
                )
            )
            if record is None:
                return None
            record.status = STATUS_INVALID
            record.last_error = error[:2000]
            record.last_validated_at = validated_at
            await session.commit()
            await session.refresh(record)
            return record

    async def revoke(self, platform: str) -> bool:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(PlatformAuthCredentialRecord).where(
                    PlatformAuthCredentialRecord.platform == platform
                )
            )
            if record is None:
                return False
            record.status = STATUS_REVOKED
            await session.commit()
            return True

    async def delete(self, platform: str) -> bool:
        async with self._database.session_factory() as session:
            record = await session.scalar(
                select(PlatformAuthCredentialRecord).where(
                    PlatformAuthCredentialRecord.platform == platform
                )
            )
            if record is None:
                return False
            await session.delete(record)
            await session.commit()
            return True

    async def list_all(self) -> list[PlatformAuthCredentialRecord]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(PlatformAuthCredentialRecord).order_by(
                    PlatformAuthCredentialRecord.platform
                )
            )
            return list(result)
