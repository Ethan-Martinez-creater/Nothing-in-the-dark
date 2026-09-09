"""Phase 5 tests: platform credential resolver (DB > env > None)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.infrastructure.database.platform_auth_repository import (
    STATUS_INVALID,
    PlatformAuthRepository,
)
from app.services.platform_auth import PlatformAuthService
from app.services.platform_credentials import (
    PlatformCredentialResolver,
    cookies_to_cookie_string,
)
from tests.memory_db import MemoryDatabase

_MASTER_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # 32 bytes


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'cred.db'}",
        "demo_mode": True,
        "platform_auth_master_key": _MASTER_KEY,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_cookies_to_cookie_string_joins_name_value() -> None:
    result = cookies_to_cookie_string(
        [
            {"name": "SUB", "value": "v1", "domain": ".weibo.com"},
            {"name": "WBPSESS", "value": "v2"},
            {"name": "", "value": "ignored"},
        ]
    )
    assert result == "SUB=v1;WBPSESS=v2"


async def _make_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **settings_overrides: object
) -> tuple[PlatformCredentialResolver, PlatformAuthService, PlatformAuthRepository, MemoryDatabase]:
    db = MemoryDatabase()
    await db.create_schema()
    repository = PlatformAuthRepository(db)
    service = PlatformAuthService(repository, _MASTER_KEY)
    resolver = PlatformCredentialResolver(service, _settings(tmp_path, **settings_overrides))
    return resolver, service, repository, db


@pytest.mark.asyncio
async def test_db_credential_takes_priority_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver, service, _, db = await _make_resolver(
        tmp_path,
        monkeypatch,
        mediacrawler_weibo_cookies="SUB=env-cookie",
    )
    try:
        # 先写 DB credential
        await service.save_credential(
            "weibo",
            {
                "format_version": 1,
                "platform": "weibo",
                "cookies": [
                    {"name": "SUB", "value": "db-cookie", "domain": ".weibo.com"}
                ],
            },
        )
        resolved = await resolver.resolve_cookie_string("weibo")
        assert resolved == "SUB=db-cookie"  # DB 优先于 env
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_env_cookie_fallback_when_no_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver, _, _, db = await _make_resolver(
        tmp_path,
        monkeypatch,
        mediacrawler_bilibili_cookies="SESSDATA=env-fallback",
    )
    try:
        resolved = await resolver.resolve_cookie_string("bilibili")
        assert resolved == "SESSDATA=env-fallback"
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_none_when_no_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver, _, _, db = await _make_resolver(tmp_path, monkeypatch)
    try:
        assert await resolver.resolve_cookie_string("zhihu") is None
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_mark_failed_invalidates_db_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver, service, repository, db = await _make_resolver(tmp_path, monkeypatch)
    try:
        await service.save_credential(
            "douyin",
            {
                "format_version": 1,
                "platform": "douyin",
                "cookies": [
                    {"name": "sessionid", "value": "x", "domain": ".douyin.com"}
                ],
            },
        )
        await resolver.mark_failed("douyin", "login failed: cookie expired")
        record = await repository.get_by_platform("douyin")
        assert record is not None
        assert record.status == STATUS_INVALID
        assert "login failed" in (record.last_error or "")
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_mark_failed_noop_without_db_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver, _, repository, db = await _make_resolver(tmp_path, monkeypatch)
    try:
        await resolver.mark_failed("weibo", "login failed")
        assert await repository.get_by_platform("weibo") is None
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_revoked_credential_not_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver, service, _, db = await _make_resolver(tmp_path, monkeypatch)
    try:
        await service.save_credential(
            "weibo",
            {
                "format_version": 1,
                "platform": "weibo",
                "cookies": [
                    {"name": "SUB", "value": "revoked", "domain": ".weibo.com"}
                ],
            },
        )
        await service.revoke("weibo")
        resolved = await resolver.resolve_cookie_string("weibo")
        assert resolved is None  # revoked -> auth_required 语义
    finally:
        await db.dispose()
