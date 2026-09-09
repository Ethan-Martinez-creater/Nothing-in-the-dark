"""Phase 2 tests: platform auth credential repository."""

from __future__ import annotations

import pytest

from app.infrastructure.database.platform_auth_repository import (
    PlatformAuthRepository,
    STATUS_ACTIVE,
    STATUS_INVALID,
    STATUS_REVOKED,
)
from tests.memory_db import MemoryDatabase


@pytest.fixture
async def repo():
    db = MemoryDatabase()
    await db.create_schema()
    yield PlatformAuthRepository(db)
    await db.dispose()


@pytest.mark.asyncio
async def test_upsert_and_read(repo: PlatformAuthRepository) -> None:
    created = await repo.upsert(
        platform="weibo",
        nonce_b64="bm9uY2U=",
        ciphertext_b64="Y2lwaGVydGV4dA==",
        account_label="main-account",
    )
    assert created.status == STATUS_ACTIVE

    loaded = await repo.get_by_platform("weibo")
    assert loaded is not None
    assert loaded.nonce_b64 == "bm9uY2U="
    assert loaded.ciphertext_b64 == "Y2lwaGVydGV4dA=="
    assert loaded.account_label == "main-account"


@pytest.mark.asyncio
async def test_upsert_same_platform_updates(repo: PlatformAuthRepository) -> None:
    await repo.upsert(
        platform="bilibili", nonce_b64="n1", ciphertext_b64="c1"
    )
    updated = await repo.upsert(
        platform="bilibili", nonce_b64="n2", ciphertext_b64="c2"
    )
    loaded = await repo.get_by_platform("bilibili")
    assert loaded is not None
    assert loaded.nonce_b64 == "n2"
    assert loaded.ciphertext_b64 == "c2"
    assert updated.platform == "bilibili"


@pytest.mark.asyncio
async def test_mark_validated_and_invalid(repo: PlatformAuthRepository) -> None:
    await repo.upsert(platform="zhihu", nonce_b64="n", ciphertext_b64="c")
    validated = await repo.mark_validated("zhihu")
    assert validated is not None
    assert validated.status == STATUS_ACTIVE
    assert validated.last_validated_at is not None

    invalid = await repo.mark_invalid("zhihu", error="cookie expired")
    assert invalid is not None
    assert invalid.status == STATUS_INVALID
    assert "expired" in (invalid.last_error or "")

    loaded = await repo.get_by_platform("zhihu")
    assert loaded is not None
    assert loaded.status == STATUS_INVALID


@pytest.mark.asyncio
async def test_revoke_and_delete(repo: PlatformAuthRepository) -> None:
    await repo.upsert(platform="tieba", nonce_b64="n", ciphertext_b64="c")
    assert await repo.revoke("tieba") is True
    loaded = await repo.get_by_platform("tieba")
    assert loaded is not None
    assert loaded.status == STATUS_REVOKED

    assert await repo.delete("tieba") is True
    assert await repo.get_by_platform("tieba") is None
    assert await repo.delete("tieba") is False


@pytest.mark.asyncio
async def test_list_all(repo: PlatformAuthRepository) -> None:
    await repo.upsert(platform="weibo", nonce_b64="n1", ciphertext_b64="c1")
    await repo.upsert(platform="douyin", nonce_b64="n2", ciphertext_b64="c2")
    platforms = [r.platform for r in await repo.list_all()]
    assert platforms == ["douyin", "weibo"]


@pytest.mark.asyncio
async def test_no_plaintext_cookie_in_persistence(
    repo: PlatformAuthRepository,
) -> None:
    """加密载荷落库后，库内不得出现明文 cookie 值。"""
    await repo.upsert(
        platform="weibo",
        nonce_b64="bm9uY2U=",
        ciphertext_b64="Y2lwaGVydGV4dA==",
    )
    loaded = await repo.get_by_platform("weibo")
    assert loaded is not None
    # 只有 base64 密文/随机数，没有原始明文结构。
    assert "opaque" not in loaded.ciphertext_b64
    assert loaded.nonce_b64.isascii()
