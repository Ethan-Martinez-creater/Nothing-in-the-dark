"""Phase 4 tests: platform auth API surface."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _make_app(tmp_path: Path, master_key: str = "") -> object:
    return create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'auth_api.db'}",
            demo_mode=True,
            platform_auth_enabled=True,
            platform_auth_master_key=master_key,
        )
    )


def test_list_platforms_returns_all_five(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/v1/platform-auth")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["platform"] for item in items] == [
        "weibo",
        "bilibili",
        "tieba",
        "zhihu",
        "douyin",
    ]
    assert all(item["status"] == "missing" for item in items)


def test_login_session_disabled_without_master_key(tmp_path: Path) -> None:
    app = _make_app(tmp_path, master_key="")
    with TestClient(app) as client:
        response = client.post("/api/v1/platform-auth/weibo/login-sessions")
    assert response.status_code == 400
    assert response.json()["code"] == "platform_auth_disabled"


def test_unknown_platform_rejected(tmp_path: Path) -> None:
    app = _make_app(tmp_path, master_key="a" * 44)
    with TestClient(app) as client:
        response = client.post("/api/v1/platform-auth/unknown/login-sessions")
    assert response.status_code == 400
    assert response.json()["code"] == "platform_auth_state_invalid"


def test_api_never_returns_secrets(tmp_path: Path) -> None:
    """GET /platform-auth 响应不得包含任何凭据/密文字段。"""
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/v1/platform-auth")
    body = response.text
    for forbidden in ("cookie", "ciphertext", "nonce", "master_key", "value"):
        assert forbidden not in body.lower()


def test_validate_returns_not_implemented(tmp_path: Path) -> None:
    app = _make_app(tmp_path, master_key="a" * 44)
    with TestClient(app) as client:
        response = client.post("/api/v1/platform-auth/weibo/validate")
    assert response.status_code == 200
    assert response.json()["result"] == "validation_not_implemented"
