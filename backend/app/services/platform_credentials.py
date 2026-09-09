"""Platform credential resolution for collection (Phase 5).

优先级（文档 D6）：
    1. 数据库 status=active 且可解密的 PlatformAuthCredential
    2. MEDIACRAWLER_<PLATFORM>_COOKIES 环境变量（legacy/operator override）
    3. 无凭据 -> None（上层返回 platform_auth_required）

认证失败回写：mark_failed 只在存在 DB credential 时标记 invalid（环境变量
来源的失败不影响 DB 状态）。
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.errors import ApplicationError
from app.infrastructure.database.platform_auth_repository import (
    STATUS_ACTIVE,
)

_COOKIE_ATTRS = {
    "weibo": "mediacrawler_weibo_cookies",
    "bilibili": "mediacrawler_bilibili_cookies",
    "tieba": "mediacrawler_tieba_cookies",
    "zhihu": "mediacrawler_zhihu_cookies",
    "douyin": "mediacrawler_douyin_cookies",
}


def cookies_to_cookie_string(cookies: list[dict[str, object]]) -> str:
    """仅将 name=value 连接为 'name=value;name2=value2'，不改写值。"""
    pairs: list[str] = []
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if name:
            pairs.append(f"{name}={value}")
    return ";".join(pairs)


class PlatformCredentialResolver:
    def __init__(
        self,
        service: Any,
        settings: Settings,
    ) -> None:
        self._service = service
        self._settings = settings

    async def resolve_cookie_string(self, platform: str) -> str | None:
        """按优先级返回 cookie 字符串；无凭据返回 None。"""
        # 1. DB credential（active + 可解密）
        if self._service.enabled:
            try:
                cookies = await self._service.load_cookies(platform)
                return cookies_to_cookie_string(cookies)
            except ApplicationError as exc:
                # platform_auth_required：无凭据，继续 fallback；
                # crypto/state 错误：load_cookies 内部已 mark_invalid，仍
                # 允许 operator env cookie 覆盖。
                if exc.code not in {
                    "platform_auth_required",
                    "platform_auth_crypto_error",
                    "platform_auth_state_invalid",
                }:
                    raise
        # 2. legacy env cookie（operator fallback / emergency override）
        attr = _COOKIE_ATTRS.get(platform)
        if attr is None:
            return None
        env_cookie = getattr(self._settings, attr, None)
        raw = str(env_cookie.get_secret_value()) if env_cookie else ""
        return raw or None

    async def mark_failed(self, platform: str, message: str) -> None:
        """认证失败回写：仅在存在 DB credential 时标记 invalid。"""
        if not self._service.enabled:
            return
        record = await self._service.get_record(platform)
        if record is not None and record.status == STATUS_ACTIVE:
            await self._service.mark_invalid(
                platform, error=message[:2000]
            )
