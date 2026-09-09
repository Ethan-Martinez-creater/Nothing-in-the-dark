"""AES-256-GCM encryption for platform auth credentials (Phase 2).

凭据载荷（cookie 列表）在落库前必须加密。本模块不接触数据库，只负责
加密/解密与 payload 结构校验。master key 来自配置（PLATFORM_AUTH_MASTER_KEY，
base64 编码的 32-byte 随机密钥），缺失或非法时平台认证功能 fail closed。
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.errors import ApplicationError

_PAYLOAD_FORMAT_VERSION = 1
_AAD_PREFIX = b"platform-auth:"
_AAD_SUFFIX = b":v1"
_MAX_PAYLOAD_BYTES = 256 * 1024
_MAX_COOKIES = 512
_MAX_COOKIE_VALUE_CHARS = 16 * 1024


def _crypto_error(message: str) -> ApplicationError:
    return ApplicationError(message, code="platform_auth_crypto_error")


@dataclass(frozen=True, slots=True)
class EncryptedPayload:
    nonce_b64: str
    ciphertext_b64: str


class PlatformAuthCipher:
    """AES-256-GCM wrapper with per-credential AAD binding."""

    def __init__(self, master_key_b64: str) -> None:
        if not master_key_b64:
            raise _crypto_error(
                "Platform auth master key is not configured"
            )
        try:
            key = base64.b64decode(master_key_b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise _crypto_error(
                "Platform auth master key is not valid base64"
            ) from exc
        if len(key) != 32:
            raise _crypto_error(
                "Platform auth master key must decode to 32 bytes"
            )
        self._key = key

    @staticmethod
    def _aad(credential_id: str) -> bytes:
        return _AAD_PREFIX + credential_id.encode("utf-8") + _AAD_SUFFIX

    def encrypt(
        self,
        *,
        credential_id: str,
        payload: dict[str, object],
    ) -> EncryptedPayload:
        payload.setdefault("format_version", _PAYLOAD_FORMAT_VERSION)
        self._validate_payload(payload)
        plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(
            nonce, plaintext, self._aad(credential_id)
        )
        return EncryptedPayload(
            nonce_b64=_b64encode(nonce),
            ciphertext_b64=_b64encode(ciphertext),
        )

    def decrypt(
        self,
        *,
        credential_id: str,
        encrypted: EncryptedPayload,
    ) -> dict[str, object]:
        try:
            nonce = _b64decode(encrypted.nonce_b64)
            ciphertext = _b64decode(encrypted.ciphertext_b64)
            plaintext = AESGCM(self._key).decrypt(
                nonce, ciphertext, self._aad(credential_id)
            )
            payload = json.loads(plaintext.decode("utf-8"))
        except ApplicationError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一为可识别错误
            raise _crypto_error(
                "Platform auth credential cannot be decrypted"
            ) from exc
        if not isinstance(payload, dict):
            raise _crypto_error("Platform auth payload is not an object")
        if payload.get("format_version") != _PAYLOAD_FORMAT_VERSION:
            raise _crypto_error(
                "Platform auth payload format_version is not supported"
            )
        self._validate_payload(payload)
        return payload

    @staticmethod
    def _validate_payload(payload: dict[str, object]) -> None:
        """入库前校验 payload 结构，避免无界输入。"""
        platform = payload.get("platform")
        if not isinstance(platform, str) or not platform:
            raise _crypto_error("Platform auth payload missing platform")
        cookies = payload.get("cookies")
        if not isinstance(cookies, list):
            raise _crypto_error("Platform auth payload cookies must be a list")
        if not cookies:
            raise _crypto_error("Platform auth payload cookies is empty")
        if len(cookies) > _MAX_COOKIES:
            raise _crypto_error("Platform auth payload has too many cookies")
        for cookie in cookies:
            if not isinstance(cookie, dict):
                raise _crypto_error("Platform auth cookie must be an object")
            for field in ("name", "value", "domain"):
                value = cookie.get(field)
                if not isinstance(value, str) or not value:
                    raise _crypto_error(
                        f"Platform auth cookie field {field!r} is invalid"
                    )
            value = str(cookie.get("value") or "")
            if len(value) > _MAX_COOKIE_VALUE_CHARS:
                raise _crypto_error("Platform auth cookie value is too long")
        serialized = json.dumps(payload, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise _crypto_error("Platform auth payload is too large")


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64decode(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise _crypto_error("Platform auth encrypted field is not valid base64") from exc


def make_master_key() -> str:
    """生成新的 32-byte master key（base64 编码），供部署使用。"""
    return _b64encode(os.urandom(32))


# 类型别名，便于 service 层引用。
PlatformAuthPayload = dict[str, Any]
