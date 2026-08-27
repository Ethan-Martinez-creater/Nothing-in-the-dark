"""MediaFetchService: 受控流式下载与内容寻址存储 (04).

- 每跳重定向前重新做 SSRF/HTTPS 校验。
- DNS rebinding 加固：解析一次并固定 IP 直连，TLS SNI / 证书校验仍使用
  原始 hostname（httpcore sni_hostname 扩展），消除校验解析与实际连接
  解析之间的窗口。
- 同时限制 Content-Length 与实际读入字节，超限立即中止。
- magic bytes 决定 MIME，不信任后缀与响应头。
- 真实字节 SHA-256 作为内容寻址键，相同字节只落盘一次。
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlunparse

import anyio
import httpx

from app.services.media_pipeline import (
    DEFAULT_LIMITS,
    UnsafeUrlError,
    is_unsafe_ip,
    sniff_mime_type,
    validate_download_url,
)

_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


async def _resolve_pinned_ip(host: str, port: int) -> str:
    """异步解析 DNS 并返回第一个安全 IP（拒绝内网/环回等）。

    返回的 IP 将直接作为连接目标，TLS SNI 与证书校验仍使用原始 hostname，
    消除“校验解析”与“实际连接解析”之间的 DNS rebinding 窗口。
    """
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"DNS 解析失败: {host}") from exc
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if not is_unsafe_ip(ip):
            return raw
    raise UnsafeUrlError(f"无安全解析结果: {host}")


@dataclass(slots=True)
class FetchResult:
    ok: bool
    storage_uri: str | None = None
    sha256: str | None = None
    byte_size: int = 0
    mime_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class MediaFetchService:
    def __init__(
        self,
        storage_root: Path,
        http_client: httpx.AsyncClient | None = None,
        limits: dict[str, Any] | None = None,
    ) -> None:
        self._storage_root = storage_root
        self._client = http_client
        self._limits = limits or dict(DEFAULT_LIMITS)

    async def fetch(self, url: str, media_type: str) -> FetchResult:
        client = self._client or httpx.AsyncClient(follow_redirects=False, trust_env=False)
        try:
            return await self._fetch_with_client(client, url, media_type)
        finally:
            if self._client is None:
                await client.aclose()

    async def _fetch_with_client(
        self,
        client: httpx.AsyncClient,
        url: str,
        media_type: str,
    ) -> FetchResult:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                parsed = validate_download_url(current)
                host = parsed.hostname
                if not host:
                    return FetchResult(
                        ok=False, error_code="unsafe_url", error_message="missing host"
                    )
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                # DNS rebinding 加固：解析一次并固定 IP，TLS SNI/证书校验
                # 仍用原始 hostname（httpcore 的 sni_hostname 扩展）。
                pinned_ip = await _resolve_pinned_ip(host, port)
                netloc = f"[{pinned_ip}]:{port}" if ":" in pinned_ip else f"{pinned_ip}:{port}"
                pinned_url = urlunparse(
                    (
                        parsed.scheme,
                        netloc,
                        parsed.path or "/",
                        parsed.params,
                        parsed.query,
                        parsed.fragment,
                    )
                )
                request = client.build_request(
                    "GET",
                    pinned_url,
                    extensions={"sni_hostname": host},
                    headers={"Host": host},
                )
                response = await client.send(request, stream=True)
                try:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            return FetchResult(
                                ok=False, error_code="redirect_without_location"
                            )
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        return FetchResult(
                            ok=False,
                            error_code="download_http_error",
                            error_message=f"HTTP {response.status_code}",
                        )
                    return await self._read_body(response, media_type)
                finally:
                    await response.aclose()
            except UnsafeUrlError as exc:
                return FetchResult(
                    ok=False, error_code="unsafe_url", error_message=str(exc)
                )
            except httpx.HTTPError as exc:
                return FetchResult(
                    ok=False, error_code="download_error", error_message=str(exc)
                )
        return FetchResult(ok=False, error_code="too_many_redirects")

    async def _read_body(
        self,
        response: httpx.Response,
        media_type: str,
    ) -> FetchResult:
        limit = int(
            self._limits.get(media_type, self._limits.get("video", 500 * 1024 * 1024))
        )
        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > limit:
            return FetchResult(
                ok=False,
                error_code="content_too_large",
                error_message=f"Content-Length {content_length} 超过上限 {limit}",
            )
        hasher = hashlib.sha256()
        total = 0
        head = b""
        self._storage_root.mkdir(parents=True, exist_ok=True)
        tmp = self._storage_root / f".tmp-{uuid.uuid4().hex}"
        try:
            async with await anyio.open_file(tmp, "wb") as handle:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > limit:
                        return FetchResult(
                            ok=False,
                            error_code="content_too_large",
                            error_message=f"实际字节超过上限 {limit}",
                        )
                    hasher.update(chunk)
                    await handle.write(chunk)
                    if len(head) < 16:
                        head = (head + chunk)[:16]
            if total == 0:
                return FetchResult(ok=False, error_code="empty_body")
            mime = sniff_mime_type(head)
            digest = hasher.hexdigest()
            storage_uri = await self._store_file(digest, tmp)
            return FetchResult(
                ok=True,
                storage_uri=storage_uri,
                sha256=digest,
                byte_size=total,
                mime_type=mime,
            )
        finally:
            tmp_path = anyio.Path(tmp)
            if await tmp_path.exists():
                await tmp_path.unlink()

    async def _store_file(self, digest: str, tmp_path: Path) -> str:
        rel = Path(digest[:2]) / digest
        target = self._storage_root / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(tmp_path.replace, target)
        return str(target)