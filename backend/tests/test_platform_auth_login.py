"""Phase 4 tests: login session coordinator logic.

不启动真实 Chromium/MediaCrawler 子进程：monkeypatch `_spawn` 为 no-op，
用假 process 对象验证终止/清理路径。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.infrastructure.database.platform_auth_repository import (
    STATUS_ACTIVE,
    PlatformAuthRepository,
)
from app.services.platform_auth import (
    SESSION_AUTHENTICATED,
    SESSION_CANCELLED,
    SESSION_EXPIRED,
    SESSION_FAILED,
    SESSION_WAITING_SCAN,
    LoginSession,
    LoginSessionCoordinator,
    PlatformAuthService,
)
from tests.memory_db import MemoryDatabase

_MASTER_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # 32 bytes


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'login.db'}",
        demo_mode=True,
        platform_auth_master_key=_MASTER_KEY,
        platform_auth_session_root=str(tmp_path / "sessions"),
        platform_auth_poll_interval_seconds=0.01,
    )


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return int(self.returncode or 0)


async def _make_coordinator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = MemoryDatabase()
    await db.create_schema()
    repository = PlatformAuthRepository(db)
    service = PlatformAuthService(repository, _MASTER_KEY)
    coordinator = LoginSessionCoordinator(_settings(tmp_path), service)

    async def noop_spawn(session: LoginSession) -> None:
        session.session_dir = Path(_settings(tmp_path).platform_auth_session_root) / session.id
        session.session_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(coordinator, "_spawn", noop_spawn)
    return coordinator, service, repository, db


@pytest.mark.asyncio
async def test_duplicate_platform_session_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator, _, _, db = await _make_coordinator(tmp_path, monkeypatch)
    try:
        first = await coordinator.start("weibo")
        second = await coordinator.start("weibo")
        assert first.id == second.id
        assert first.status in {"starting", "waiting_scan"}
        await coordinator.cancel(first.id)
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_ttl_expires_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator, _, _, db = await _make_coordinator(tmp_path, monkeypatch)
    try:
        session = await coordinator.start("weibo")
        # 把过期时间拨到过去，watcher 首轮即 expired。
        from datetime import UTC, datetime, timedelta

        session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await asyncio.wait_for(session.watcher, timeout=5)
        assert session.status == SESSION_EXPIRED
        assert session.error_code == "platform_auth_session_expired"
        assert not (session.session_dir / "qr.json").exists()
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_cancel_terminates_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator, _, _, db = await _make_coordinator(tmp_path, monkeypatch)
    try:
        session = await coordinator.start("weibo")
        fake = _FakeProcess()
        session.process = fake  # type: ignore[assignment]
        await coordinator.cancel(session.id)
        assert fake.terminated is True
        assert session.status == SESSION_CANCELLED
        # cancel 后 registry 已移除
        from app.core.errors import ApplicationError

        with pytest.raises(ApplicationError):
            await coordinator.get(session.id)
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_auth_state_malformed_marks_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator, _, _, db = await _make_coordinator(tmp_path, monkeypatch)
    try:
        session = await coordinator.start("bilibili")
        assert session.session_dir is not None
        (session.session_dir / "auth_state.json").write_text(
            "{not-json", encoding="utf-8"
        )
        await asyncio.wait_for(session.watcher, timeout=5)
        assert session.status == SESSION_FAILED
        assert session.error_code == "platform_auth_state_invalid"
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_auth_state_success_saves_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator, _, repository, db = await _make_coordinator(tmp_path, monkeypatch)
    try:
        session = await coordinator.start("weibo")
        assert session.session_dir is not None
        (session.session_dir / "auth_state.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "platform": "weibo",
                    "cookies": [
                        {
                            "name": "SUB",
                            "value": "opaque-token",
                            "domain": ".weibo.com",
                            "path": "/",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        await asyncio.wait_for(session.watcher, timeout=5)
        assert session.status == SESSION_AUTHENTICATED
        record = await repository.get_by_platform("weibo")
        assert record is not None
        assert record.status == STATUS_ACTIVE
        # 密文不包含明文 cookie
        assert "opaque-token" not in record.ciphertext_b64
        # 敏感临时文件已清理
        assert not (session.session_dir / "auth_state.json").exists()
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_qr_file_flips_to_waiting_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator, _, _, db = await _make_coordinator(tmp_path, monkeypatch)
    try:
        session = await coordinator.start("zhihu")
        assert session.session_dir is not None
        (session.session_dir / "qr.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "type": "qrcode",
                    "data_url": "data:image/png;base64,qr-data",
                }
            ),
            encoding="utf-8",
        )
        for _ in range(20):
            refreshed = await coordinator.get(session.id)
            if refreshed.status == SESSION_WAITING_SCAN:
                break
            await asyncio.sleep(0.02)
        assert session.status == SESSION_WAITING_SCAN
        assert session.qr_code == "data:image/png;base64,qr-data"
        await coordinator.cancel(session.id)
    finally:
        await db.dispose()


async def test_bootstrap_master_key_expression_roundtrips(tmp_path: Path) -> None:
    """回归：bootstrap 必须用 get_secret_value() 传主密钥。

    str(SecretStr) 返回掩码 '**********'——enabled 检查仍为真、二维码正常，
    但 save_credential 加密时才以 "not valid base64" 失败（服务器实测踩坑）。
    """
    from app.infrastructure.security.platform_auth_cipher import PlatformAuthCipher

    settings = _settings(tmp_path)
    db = MemoryDatabase()
    await db.create_schema()

    # bootstrap 同款表达式：get_secret_value() 取真实值，加解密闭环可用。
    service = PlatformAuthService(
        PlatformAuthRepository(db),
        settings.platform_auth_master_key.get_secret_value(),
    )
    try:
        await service.save_credential(
            "weibo",
            {
                "format_version": 1,
                "platform": "weibo",
                "cookies": [
                    {
                        "name": "SUB",
                        "value": "opaque-token",
                        "domain": ".weibo.com",
                        "path": "/",
                    }
                ],
            },
        )
        cookies = await service.load_cookies("weibo")
        assert cookies == [
            {
                "name": "SUB",
                "value": "opaque-token",
                "domain": ".weibo.com",
                "path": "/",
            }
        ]
    finally:
        await db.dispose()

    # 反向锁定：掩码字符串必须被 cipher 拒绝，防止有人改回 str(SecretStr)。
    with pytest.raises(Exception, match="not valid base64"):
        PlatformAuthCipher(str(settings.platform_auth_master_key))
