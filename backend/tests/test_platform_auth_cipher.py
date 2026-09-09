"""Phase 2 tests: AES-256-GCM platform auth credential cipher."""

from __future__ import annotations

import base64
import os

import pytest

from app.core.errors import ApplicationError
from app.infrastructure.security.platform_auth_cipher import (
    EncryptedPayload,
    PlatformAuthCipher,
)

_PAYLOAD: dict[str, object] = {
    "format_version": 1,
    "platform": "weibo",
    "cookies": [
        {
            "name": "SUB",
            "value": "opaque-token-value",
            "domain": ".weibo.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
        }
    ],
}


def _key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


def test_round_trip() -> None:
    cipher = PlatformAuthCipher(_key_b64())
    encrypted = cipher.encrypt(credential_id="cred-1", payload=_PAYLOAD)
    decrypted = cipher.decrypt(credential_id="cred-1", encrypted=encrypted)
    assert decrypted["platform"] == "weibo"
    assert decrypted["cookies"] == _PAYLOAD["cookies"]


def test_nonce_is_unique_per_encrypt() -> None:
    cipher = PlatformAuthCipher(_key_b64())
    first = cipher.encrypt(credential_id="cred-1", payload=_PAYLOAD)
    second = cipher.encrypt(credential_id="cred-1", payload=_PAYLOAD)
    assert first.nonce_b64 != second.nonce_b64
    assert first.ciphertext_b64 != second.ciphertext_b64


def test_tampered_ciphertext_fails() -> None:
    cipher = PlatformAuthCipher(_key_b64())
    encrypted = cipher.encrypt(credential_id="cred-1", payload=_PAYLOAD)
    raw = bytearray(base64.b64decode(encrypted.ciphertext_b64))
    raw[-1] ^= 0x01
    tampered = EncryptedPayload(
        nonce_b64=encrypted.nonce_b64,
        ciphertext_b64=base64.b64encode(bytes(raw)).decode("ascii"),
    )
    with pytest.raises(ApplicationError) as exc:
        cipher.decrypt(credential_id="cred-1", encrypted=tampered)
    assert exc.value.code == "platform_auth_crypto_error"


def test_wrong_key_fails() -> None:
    encrypted = PlatformAuthCipher(_key_b64()).encrypt(
        credential_id="cred-1", payload=_PAYLOAD
    )
    with pytest.raises(ApplicationError):
        PlatformAuthCipher(_key_b64()).decrypt(
            credential_id="cred-1", encrypted=encrypted
        )


def test_wrong_credential_id_aad_fails() -> None:
    cipher = PlatformAuthCipher(_key_b64())
    encrypted = cipher.encrypt(credential_id="cred-1", payload=_PAYLOAD)
    with pytest.raises(ApplicationError):
        cipher.decrypt(credential_id="cred-2", encrypted=encrypted)


def test_missing_key_fails_closed() -> None:
    with pytest.raises(ApplicationError) as exc:
        PlatformAuthCipher("")
    assert exc.value.code == "platform_auth_crypto_error"


def test_invalid_base64_key_fails() -> None:
    with pytest.raises(ApplicationError):
        PlatformAuthCipher("not-base64!!")


def test_wrong_key_length_fails() -> None:
    short = base64.b64encode(b"short-key").decode("ascii")
    with pytest.raises(ApplicationError):
        PlatformAuthCipher(short)


def test_unsupported_format_version_fails() -> None:
    cipher = PlatformAuthCipher(_key_b64())
    payload = dict(_PAYLOAD)
    payload["format_version"] = 99
    encrypted = cipher.encrypt(credential_id="cred-1", payload=payload)
    with pytest.raises(ApplicationError):
        cipher.decrypt(credential_id="cred-1", encrypted=encrypted)


def test_payload_schema_validation() -> None:
    cipher = PlatformAuthCipher(_key_b64())
    with pytest.raises(ApplicationError):
        cipher.encrypt(
            credential_id="cred-1",
            payload={"platform": "weibo", "cookies": "not-a-list"},
        )
    with pytest.raises(ApplicationError):
        cipher.encrypt(
            credential_id="cred-1",
            payload={"platform": "weibo", "cookies": []},
        )
    with pytest.raises(ApplicationError):
        cipher.encrypt(
            credential_id="cred-1",
            payload={
                "platform": "weibo",
                "cookies": [{"name": "SUB"}],  # missing value/domain
            },
        )
    with pytest.raises(ApplicationError):
        cipher.encrypt(
            credential_id="cred-1",
            payload={
                "platform": "weibo",
                "cookies": [
                    {
                        "name": "SUB",
                        "value": "x" * 20_000,  # value too long
                        "domain": ".weibo.com",
                    }
                ],
            },
        )


def test_ciphertext_does_not_contain_plaintext() -> None:
    cipher = PlatformAuthCipher(_key_b64())
    encrypted = cipher.encrypt(credential_id="cred-1", payload=_PAYLOAD)
    raw = base64.b64decode(encrypted.ciphertext_b64)
    assert b"opaque-token-value" not in raw
    assert "opaque-token-value" not in encrypted.ciphertext_b64
