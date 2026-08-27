"""Tests for multimodal media pipeline (04)."""

from __future__ import annotations

import asyncio
import atexit
import ipaddress
import shutil
import socket
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.application.media_pipeline_worker import MediaPipelineWorker
from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.core.errors import ResourceNotFoundError
from app.infrastructure.database import Database
from app.infrastructure.database.media_pipeline_repository import MediaPipelineRepository
from app.infrastructure.media_fetch import FetchResult, MediaFetchService
from app.main import create_app
from app.schemas.cases import CreateCaseRequest
from app.services import media_pipeline as mp

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-media-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


def _tmp_dir() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-media-dir-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d


# ---------- SSRF URL 校验 --------------------------------------------------


def test_is_unsafe_ip() -> None:
    assert mp.is_unsafe_ip(ipaddress.ip_address("127.0.0.1")) is True
    assert mp.is_unsafe_ip(ipaddress.ip_address("10.0.0.1")) is True
    assert mp.is_unsafe_ip(ipaddress.ip_address("192.168.1.1")) is True
    assert mp.is_unsafe_ip(ipaddress.ip_address("169.254.1.1")) is True
    assert mp.is_unsafe_ip(ipaddress.ip_address("::1")) is True
    assert mp.is_unsafe_ip(ipaddress.ip_address("fc00::1")) is True
    assert mp.is_unsafe_ip(ipaddress.ip_address("8.8.8.8")) is False


def test_validate_url_rejects_http_and_credentials() -> None:
    with pytest.raises(mp.UnsafeUrlError):
        mp.validate_download_url("http://example.com/x.jpg")
    with pytest.raises(mp.UnsafeUrlError):
        mp.validate_download_url("https://user:pass@example.com/x.jpg")


def test_validate_url_rejects_private_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))],
    )
    with pytest.raises(mp.UnsafeUrlError):
        mp.validate_download_url("https://evil.example/x.jpg")


def test_validate_url_accepts_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
    )
    parsed = mp.validate_download_url("https://example.com/x.jpg")
    assert parsed.scheme == "https"


# ---------- magic bytes / 哈希 / C2PA --------------------------------------


def test_sniff_mime_types() -> None:
    assert mp.sniff_mime_type(bytes.fromhex("ffd8ff")) == "image/jpeg"
    assert mp.sniff_mime_type(bytes.fromhex("89504e470d0a1a0a")) == "image/png"
    assert mp.sniff_mime_type(b"GIF89a") == "image/gif"
    assert mp.sniff_mime_type(bytes.fromhex("00000018") + b"ftyp") == "video/mp4"
    assert mp.sniff_mime_type(b"ID3") == "audio/mpeg"
    # 伪装：后缀/响应 MIME 不参与，纯字节判断。
    assert mp.sniff_mime_type(b"plain text not an image") is None


def test_sha256_bytes_real() -> None:
    assert mp.sha256_bytes(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_detect_c2pa_five_states() -> None:
    assert mp.detect_c2pa(b"").status == "error"
    assert mp.detect_c2pa(b"no content credentials").status == "not_present"
    assert mp.detect_c2pa(b"prefix JUMBF more").status == "unsupported"
    assert mp.detect_c2pa(b"prefix c2pa more").status == "unsupported"


def test_probe_image_dimensions() -> None:
    # PNG: signature + IHDR(width=100,height=50)
    png = (
        bytes.fromhex("89504e470d0a1a0a")
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (100).to_bytes(4, "big")
        + (50).to_bytes(4, "big")
        + bytes([8, 6, 0, 0, 0])
    )
    assert mp.probe_image_dimensions(png, "image/png") == (100, 50)
    # GIF logical screen (width=16, height=8 little-endian)
    gif = b"GIF89a" + (16).to_bytes(2, "little") + (8).to_bytes(2, "little")
    assert mp.probe_image_dimensions(gif, "image/gif") == (16, 8)
    assert mp.probe_image_dimensions(b"junk", None) is None


# ---------- MediaFetchService（MockTransport） ------------------------------


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _mock_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
    )


async def test_fetch_downloads_and_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_public_dns(monkeypatch)
    payload = bytes.fromhex("89504e470d0a1a0a") + b"fakedata"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    service = MediaFetchService(_tmp_dir(), http_client=_client(handler))
    result = await service.fetch("https://example.com/x.png", "image")
    assert result.ok is True
    assert result.sha256 == mp.sha256_bytes(payload)
    assert result.mime_type == "image/png"
    assert result.byte_size == len(payload)
    # 内容寻址落盘路径以真实 sha256 结尾。
    assert result.storage_uri is not None
    assert result.sha256 is not None
    assert result.storage_uri.endswith(result.sha256)


async def test_fetch_rejects_oversize_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_public_dns(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x", headers={"content-length": "999999999"})

    service = MediaFetchService(_tmp_dir(), http_client=_client(handler))
    result = await service.fetch("https://example.com/x.jpg", "image")
    assert result.ok is False and result.error_code == "content_too_large"


async def test_fetch_rejects_redirect_to_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        # DNS rebinding 加固：连接目标固定为解析出的 IP，域名经 sni_hostname
        # 扩展与 Host 头保留，供 TLS 证书校验与虚拟主机路由。
        assert request.url.host == "8.8.8.8"
        assert request.extensions.get("sni_hostname") == "example.com"
        if request.headers.get("host") == "example.com":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/x.jpg"})
        return httpx.Response(200, content=b"x")

    service = MediaFetchService(_tmp_dir(), http_client=_client(handler))
    result = await service.fetch("https://example.com/x.jpg", "image")
    assert result.ok is False and result.error_code == "unsafe_url"


# ---------- MediaPipelineWorker 集成 --------------------------------------


class _FakeFetch:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def fetch(self, url: str, media_type: str) -> FetchResult:
        d = _tmp_dir()
        target = d / "asset.bin"
        target.write_bytes(self._payload)
        return FetchResult(
            ok=True,
            storage_uri=str(target),
            sha256=mp.sha256_bytes(self._payload),
            byte_size=len(self._payload),
            mime_type="image/png",
        )


async def _build_asset(db_path: Path) -> tuple[MediaPipelineRepository, str]:
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="媒体流水线", platforms=["weibo"]))
    asset = await app_repo.create_media_asset(
        case_id=case.id,
        post_id=None,
        platform="weibo",
        media_type="image",
        url="https://example.com/x.png",
        normalized_url="https://example.com/x.png",
    )
    return MediaPipelineRepository(database), asset.id


async def test_worker_full_pipeline() -> None:
    payload = (
        bytes.fromhex("89504e470d0a1a0a")
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (100).to_bytes(4, "big")
        + (50).to_bytes(4, "big")
        + bytes([8, 6, 0, 0, 0])
    )
    repo, asset_id = await _build_asset(_tmp_db())
    worker = MediaPipelineWorker(
        repo,
        MediaFetchService(_tmp_dir()),
        enabled=False,
    )
    worker._fetch = _FakeFetch(payload)  # type: ignore[assignment]

    # 驱动多轮 tick 直到资产分析完成。
    for _ in range(12):
        await worker.tick()
        asset = await repo.get_asset(asset_id)
        if asset.analysis_status == "succeeded":
            break

    asset = await repo.get_asset(asset_id)
    assert asset.download_status == "downloaded"
    assert asset.analysis_status == "succeeded"
    assert asset.actual_sha256 == mp.sha256_bytes(payload)
    assert asset.hash_kind == "sha256"
    assert asset.width == 100 and asset.height == 50
    assert asset.c2pa_status == "not_present"


async def test_worker_job_idempotent() -> None:
    repo, asset_id = await _build_asset(_tmp_db())
    # 重复创建 download job 只保留一个（唯一约束）。
    first = await repo.create_job(asset_id, "download")
    second = await repo.create_job(asset_id, "download")
    assert first is not None and second is None


async def test_repository_unknown_asset_raises() -> None:
    repo, _ = await _build_asset(_tmp_db())
    with pytest.raises(ResourceNotFoundError):
        await repo.get_asset("no-such-asset")


# ---------- API -----------------------------------------------------------


def test_api_list_media() -> None:
    db_path = _tmp_db()
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    asyncio.run(database.create_schema())
    app_repo = ApplicationRepository(database)

    async def seed() -> str:
        case = await app_repo.create_case(CreateCaseRequest(topic="媒体 API", platforms=["weibo"]))
        await app_repo.create_media_asset(
            case_id=case.id,
            post_id=None,
            platform="weibo",
            media_type="image",
            url="https://example.com/x.png",
            normalized_url="https://example.com/x.png",
        )
        return case.id

    case_id = asyncio.run(seed())
    asyncio.run(database.dispose())

    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            demo_mode=True,
            media_pipeline_enabled=False,
        )
    )
    with TestClient(app) as client:
        response = client.get(f"/api/v1/cases/{case_id}/media")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 1
        assert payload[0]["media_type"] == "image"
        assert payload[0]["download_status"] == "not_downloaded"
        assert payload[0]["hash_kind"] == "url_fingerprint_legacy"


def test_real_provider_capability_states_are_honest() -> None:
    from app.infrastructure.media_providers import C2PAToolProvider, FFprobeProvider

    assert FFprobeProvider("definitely-missing-ffprobe").available is False
    provider = C2PAToolProvider("definitely-missing-c2patool")
    result = asyncio.run(provider.verify("missing", b"c2pa"))
    assert result.status == "unsupported"


async def test_terminal_media_failure_marks_asset_partial() -> None:
    media_repo, asset_id = await _build_asset(_tmp_db())
    job = await media_repo.create_job(asset_id, "ocr")
    assert job is not None
    await media_repo.update_job(job.id, status="failed_terminal", attempt=3)
    worker = MediaPipelineWorker(
        media_repo,
        MediaFetchService(_tmp_db().parent),
        enabled=False,
    )
    await worker._finalize_asset_if_done(asset_id)
    updated = await media_repo.get_asset(asset_id)
    assert updated.analysis_status == "partial"


async def test_expired_final_media_attempt_becomes_partial() -> None:
    repo, asset_id = await _build_asset(_tmp_db())
    job = await repo.create_job(asset_id, "ocr")
    assert job is not None
    claimed = await repo.claim_job("dead-worker", 0, max_attempts=1)
    assert claimed is not None and claimed.attempt == 1
    assert await repo.terminalize_expired_jobs(1) == [asset_id]
    worker = MediaPipelineWorker(repo, MediaFetchService(_tmp_dir()), max_attempts=1, enabled=False)
    await worker._finalize_asset_if_done(asset_id)
    current_job = await repo.get_job(job.id)
    current_asset = await repo.get_asset(asset_id)
    assert current_job.status == "failed_terminal"
    assert current_asset.analysis_status == "partial"
