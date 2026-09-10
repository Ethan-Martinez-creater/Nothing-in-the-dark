"""Platform auth service and login session coordinator (Phase 4).

PlatformAuthService
    凭据管理：加密保存、状态查询、撤销、失效标记（repository + cipher
    组合，明文 cookie 不经过 API 层）。

LoginSessionCoordinator
    MediaCrawler QR 登录会话：启动 auth 子进程（复用 mediacrawler_entry
    + COIFESP_AUTH_SESSION_DIR bridge）、轮询 qr.json / auth_state.json、
    TTL/取消/清理。会话状态存内存 + session 目录临时文件，不写正式库。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any

from app.core.config import Settings
from app.core.errors import ApplicationError
from app.infrastructure.crawler.mediacrawler import PLATFORM_CODES
from app.infrastructure.database.platform_auth_repository import (
    STATUS_ACTIVE,
    PlatformAuthRepository,
)
from app.infrastructure.security.platform_auth_cipher import (
    EncryptedPayload,
    PlatformAuthCipher,
)

PLATFORMS = ("weibo", "bilibili", "tieba", "zhihu", "douyin")

SESSION_STARTING = "starting"
SESSION_WAITING_SCAN = "waiting_scan"
SESSION_AUTHENTICATED = "authenticated"
SESSION_FAILED = "failed"
SESSION_EXPIRED = "expired"
SESSION_CANCELLED = "cancelled"

_QR_FILE = "qr.json"
_AUTH_STATE_FILE = "auth_state.json"


def _auth_error(message: str, code: str) -> ApplicationError:
    return ApplicationError(message, code=code)


class PlatformAuthService:
    """平台凭据的加密存取与状态管理。"""

    def __init__(
        self,
        repository: PlatformAuthRepository,
        master_key_b64: str,
        *,
        enabled: bool = True,
    ) -> None:
        self._repository = repository
        self._master_key_b64 = master_key_b64
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self._master_key_b64)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise _auth_error(
                "Platform auth is not configured",
                code="platform_auth_disabled",
            )

    def _cipher(self) -> PlatformAuthCipher:
        # 每次按需构造；key 缺失/非法时抛 platform_auth_crypto_error（fail closed）。
        return PlatformAuthCipher(self._master_key_b64)

    async def list_status(self) -> list[dict[str, object]]:
        """五个平台的状态汇总（不返回任何密文/明文）。"""
        records = {
            record.platform: record for record in await self._repository.list_all()
        }
        items: list[dict[str, object]] = []
        for platform in PLATFORMS:
            record = records.get(platform)
            if record is None:
                items.append(
                    {
                        "platform": platform,
                        "status": "missing",
                        "last_validated_at": None,
                        "expires_at": None,
                        "account_label": None,
                    }
                )
                continue
            items.append(
                {
                    "platform": platform,
                    "status": record.status,
                    "last_validated_at": (
                        record.last_validated_at.isoformat()
                        if record.last_validated_at
                        else None
                    ),
                    "expires_at": (
                        record.expires_at.isoformat() if record.expires_at else None
                    ),
                    "account_label": record.account_label,
                }
            )
        return items

    async def save_credential(
        self,
        platform: str,
        payload: dict[str, object],
        *,
        account_label: str | None = None,
    ) -> dict[str, object]:
        """校验 payload → 加密 → upsert。payload 必须含 platform 字段。"""
        self._require_enabled()
        if platform not in PLATFORM_CODES:
            raise _auth_error(
                f"Unsupported platform {platform!r}",
                code="platform_auth_state_invalid",
            )
        credential_id = f"{platform}:{account_label or 'default'}"
        encrypted = self._cipher().encrypt(
            credential_id=credential_id,
            payload=payload,
        )
        record = await self._repository.upsert(
            platform=platform,
            nonce_b64=encrypted.nonce_b64,
            ciphertext_b64=encrypted.ciphertext_b64,
            account_label=account_label,
            status=STATUS_ACTIVE,
            last_validated_at=datetime.now(UTC),
        )
        return {
            "platform": record.platform,
            "status": record.status,
            "updated_at": record.updated_at.isoformat(),
        }

    async def load_cookies(self, platform: str) -> list[dict[str, object]]:
        """解密并返回 cookies 列表（供采集注入；不外露到 API）。"""
        self._require_enabled()
        record = await self._repository.get_by_platform(platform)
        if record is None or record.status != STATUS_ACTIVE:
            raise _auth_error(
                f"Platform {platform} requires login",
                code="platform_auth_required",
            )
        credential_id = f"{platform}:{record.account_label or 'default'}"
        try:
            payload = self._cipher().decrypt(
                credential_id=credential_id,
                encrypted=EncryptedPayload(
                    nonce_b64=record.nonce_b64,
                    ciphertext_b64=record.ciphertext_b64,
                ),
            )
        except ApplicationError as exc:
            await self._repository.mark_invalid(
                platform, error=f"decrypt failed: {exc.code}"
            )
            raise
        cookies = payload.get("cookies")
        if not isinstance(cookies, list):
            raise _auth_error(
                f"Platform {platform} credential payload is invalid",
                code="platform_auth_state_invalid",
            )
        return cookies

    async def mark_invalid(self, platform: str, *, error: str) -> None:
        await self._repository.mark_invalid(platform, error=error)

    async def get_record(self, platform: str) -> Any:
        """返回原始凭据记录（供 resolver 判断来源；不经 API 暴露）。"""
        return await self._repository.get_by_platform(platform)

    async def mark_validated(self, platform: str) -> None:
        await self._repository.mark_validated(platform)

    async def revoke(self, platform: str) -> bool:
        """撤销：物理删除凭据（删除无需密钥，disabled 时也可用）。"""
        return await self._repository.delete(platform)

    async def validate(self, platform: str) -> dict[str, object]:
        """轻量验证。第一版不伪造成功；真实采集失败时会回写 invalid。"""
        self._require_enabled()
        return {
            "platform": platform,
            "result": "validation_not_implemented",
        }


@dataclass
class LoginSession:
    id: str
    platform: str
    status: str = SESSION_STARTING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    session_dir: Path | None = None
    process: asyncio.subprocess.Process | None = None
    probe_log: IO[Any] | None = None
    watcher: asyncio.Task[Any] | None = None
    qr_code: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class LoginSessionCoordinator:
    """同一平台同时只允许一个活动登录会话。"""

    def __init__(
        self,
        settings: Settings,
        service: PlatformAuthService,
    ) -> None:
        self._settings = settings
        self._service = service
        self._sessions: dict[str, LoginSession] = {}

    async def start(self, platform: str) -> LoginSession:
        if not self._service.enabled:
            raise _auth_error(
                "Platform auth is not configured",
                code="platform_auth_disabled",
            )
        if platform not in PLATFORM_CODES:
            raise _auth_error(
                f"Unsupported platform {platform!r}",
                code="platform_auth_state_invalid",
            )
        existing = self._find_active(platform)
        if existing is not None:
            return existing
        session = LoginSession(
            id=f"{platform}-{os.urandom(6).hex()}",
            platform=platform,
        )
        session.expires_at = session.created_at + timedelta(
            seconds=self._settings.platform_auth_session_ttl_seconds
        )
        self._sessions[session.id] = session
        try:
            await self._spawn(session)
        except Exception as exc:  # noqa: BLE001 - 启动失败标记 failed
            session.status = SESSION_FAILED
            session.error_code = "platform_auth_login_failed"
            session.error_message = str(exc)[:300]
            return session
        session.watcher = asyncio.create_task(self._watch(session))
        return session

    def _find_active(self, platform: str) -> LoginSession | None:
        for session in self._sessions.values():
            if (
                session.platform == platform
                and session.status in {SESSION_STARTING, SESSION_WAITING_SCAN}
            ):
                return session
        return None

    async def get(self, session_id: str) -> LoginSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise _auth_error(
                "Login session not found",
                code="platform_auth_session_not_found",
            )
        self._refresh_qr(session)
        return session

    async def cancel(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise _auth_error(
                "Login session not found",
                code="platform_auth_session_not_found",
            )
        if session.status in {SESSION_STARTING, SESSION_WAITING_SCAN}:
            session.status = SESSION_CANCELLED
        await self._terminate(session)
        self._cleanup(session)
        self._sessions.pop(session_id, None)

    # ------------------------------------------------------------------
    # 子进程与轮询
    # ------------------------------------------------------------------

    async def _spawn(self, session: LoginSession) -> None:
        settings = self._settings
        session_root = settings.platform_auth_session_root
        # 必须 resolve 为绝对路径：子进程 cwd 是 MediaCrawler 根目录，
        # 相对路径（如 ./data/auth_sessions）会在错误位置写 qr.json。
        session_dir = (Path(session_root) / session.id).resolve()
        session_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(session_dir, 0o700)
        except OSError:
            pass
        session.session_dir = session_dir

        python = str(
            settings.mediacrawler_python_executable or sys.executable
        )
        entrypoint = (
            settings.mediacrawler_entrypoint
            or Path("./scripts/mediacrawler_entry.py")
        ).resolve()  # 相对路径基于 backend cwd 解析为绝对路径（子进程 cwd 是 MediaCrawler 根）
        crawler_root = Path(settings.mediacrawler_root).resolve()  # noqa: ASYNC240 - 轻量本地路径解析
        code = PLATFORM_CODES[session.platform]
        command = [
            python,
            str(entrypoint),
            "--platform",
            code,
            "--lt",
            "qrcode",
            "--type",
            "search",
            "--keywords",
            session.platform,
            "--save_data_option",
            "jsonl",
            "--save_data_path",
            str(session_dir),
            "--crawler_max_notes_count",
            "1",
            "--max_comments_count_singlenotes",
            "0",
            "--get_comment",
            "false",
            "--headless",
            "true",
            "--max_concurrency_num",
            "1",
        ]
        # auth 子进程是受信的 MediaCrawler 代码：继承完整环境（实测过滤
        # 环境会导致 Chrome 启动异常/页面加载失败），仅覆盖 auth 专用变量。
        # 代码层保证不打印 cookie/环境（文档 10.1）。
        environment = os.environ.copy()
        environment["COIFESP_AUTH_SESSION_DIR"] = str(session_dir)
        environment["COIFESP_AUTH_PLATFORM"] = session.platform
        # systemd 服务环境没有登录会话变量；Chrome/Playwright 需要
        # XDG_RUNTIME_DIR。注意：**不要**设置 DBUS_SESSION_BUS_ADDRESS——
        # 服务器无桌面会话时该 socket 不存在，Chrome 连接 D-Bus 失败会
        # FATAL abort（实测 exit 21）。无 D-Bus 时 Chrome 正常工作。
        environment.setdefault(
            "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
        )
        # 诊断：auth 子进程输出写入 session 目录（bridge 不打印 cookie，
        # 日志安全；终态 cleanup 时删除）。
        probe_log = (session_dir / "auth_probe.log").open("ab")
        session.probe_log = probe_log
        session.process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(crawler_root),
            env=environment,
            stdout=probe_log,
            stderr=probe_log,
        )

    async def _watch(self, session: LoginSession) -> None:
        try:
            while True:
                if session.session_dir is None:
                    break
                auth_state = session.session_dir / _AUTH_STATE_FILE
                if auth_state.is_file():
                    await self._consume_auth_state(session, auth_state)
                    return
                self._refresh_qr(session)
                if datetime.now(UTC) > session.expires_at:
                    session.status = SESSION_EXPIRED
                    session.error_code = "platform_auth_session_expired"
                    await self._terminate(session)
                    self._cleanup(session)
                    return
                if (
                    session.process is not None
                    and session.process.returncode is not None
                    and session.status
                    in {SESSION_STARTING, SESSION_WAITING_SCAN}
                ):
                    session.status = SESSION_FAILED
                    session.error_code = "platform_auth_login_failed"
                    # 未导出 auth_state 就退出：要么登录被跳过且未导出
                    # （旧版 browser_data 有登录态时），要么子进程出错。
                    session.error_message = (
                        "MediaCrawler exited without exporting login state "
                        f"(exit code {session.process.returncode})"
                    )
                    await self._terminate(session)
                    self._cleanup(session)
                    return
                await asyncio.sleep(
                    self._settings.platform_auth_poll_interval_seconds
                )
        except asyncio.CancelledError:
            await self._terminate(session)
            self._cleanup(session)
            raise

    def _refresh_qr(self, session: LoginSession) -> None:
        # 终态不可逆：authenticated/failed 设置后，_terminate 等待子进程
        # 退出期间（最长 8s）qr.json 仍在磁盘上，此时并发 GET 的轮询
        # 不能把状态覆盖回 waiting_scan（实测导致 Modal 永不关闭）。
        if session.status not in {SESSION_STARTING, SESSION_WAITING_SCAN}:
            return
        if session.session_dir is None:
            return
        qr_file = session.session_dir / _QR_FILE
        try:
            payload = json.loads(qr_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if payload.get("type") == "qrcode" and payload.get("data_url"):
            session.qr_code = str(payload["data_url"])
            session.status = SESSION_WAITING_SCAN

    async def _consume_auth_state(
        self, session: LoginSession, auth_state: Path
    ) -> None:
        try:
            payload = json.loads(
                auth_state.read_text(encoding="utf-8")  # noqa: ASYNC240 - 轻量本地文件读取
            )
            cookies = payload.get("cookies")
            if not isinstance(cookies, list) or not cookies:
                raise ValueError("auth_state cookies missing")
            await self._service.save_credential(
                session.platform,
                {
                    "format_version": 1,
                    "platform": session.platform,
                    "cookies": cookies,
                },
            )
        except Exception as exc:  # noqa: BLE001 - 状态损坏统一 failed
            session.status = SESSION_FAILED
            session.error_code = "platform_auth_state_invalid"
            session.error_message = str(exc)[:300]
        else:
            session.status = SESSION_AUTHENTICATED
        finally:
            # 先删消费完的临时文件再等子进程退出：terminate 的 await wait
            # 期间轮询仍可能触发 _refresh_qr，文件先删可消除覆盖窗口。
            self._cleanup(session)
            await self._terminate(session)

    async def _terminate(self, session: LoginSession) -> None:
        process = session.process
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=8)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            except ProcessLookupError:
                pass
        # 子进程已结束后释放诊断日志句柄（_spawn 每次会话打开一个新句柄）。
        if session.probe_log is not None:
            try:
                session.probe_log.close()
            except OSError:
                pass
            session.probe_log = None

    def _cleanup(self, session: LoginSession) -> None:
        if session.session_dir is None:
            return
        # 敏感临时文件（qr.json / auth_state.json）在终态后删除。
        for name in (_QR_FILE, _AUTH_STATE_FILE):
            try:
                (session.session_dir / name).unlink(missing_ok=True)
            except OSError:
                pass
