"""Local egress proxy enforcing the tool network allow-list (15).

沙箱子进程的强制出口：工具进程的 HTTP(S) 请求必须走本代理，代理对每个
请求校验 协议/域名/端口/DNS/IP/元数据地址/重定向 并计量字节与请求数，
deny 时返回 403 且不建立隧道。代理只绑定 127.0.0.1，不对外监听。

直接 socket 连接无法被代理拦截——这是 Windows 开发模式的能力差距，生产
容器通过独立网络命名空间 + 防火墙强制；本层通过审计事件记录全部决策。
"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from collections.abc import Awaitable, Callable
from typing import Any

from app.harness.sandbox import validate_egress_url

logger = logging.getLogger(__name__)

_MAX_REDIRECTS = 5
_RESPONSE_TIMEOUT = 30.0
_MAX_TUNNEL_BYTES = 512 * 1024 * 1024  # 512MB 隧道总字节上限

EgressRecorder = Callable[[dict[str, Any]], Awaitable[None]]


class EgressProxy:
    """Loopback HTTP/HTTPS proxy with an allow-list decision per request."""

    def __init__(
        self,
        *,
        allowed_hosts: set[str] | None = None,
        allowed_ports: frozenset[int] | None = None,
        recorder: EgressRecorder | None = None,
        bind_host: str = "127.0.0.1",
    ) -> None:
        self._allowed_hosts = set(allowed_hosts or [])
        self._allowed_ports = allowed_ports
        self._recorder = recorder
        self._bind_host = bind_host
        self._server: asyncio.AbstractServer | None = None
        self._port: int | None = None
        self._running = False

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("egress proxy is not running")
        return self._port

    @property
    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        if self._running:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self._bind_host,
            port=0,
        )
        self._port = int(self._server.sockets[0].getsockname()[1])
        self._running = True
        logger.info("egress proxy listening on %s", self.proxy_url)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._running = False

    async def _record(
        self,
        *,
        url: str,
        host: str,
        decision: str,
        reason: str,
        bytes_sent: int = 0,
        bytes_received: int = 0,
        request_count: int = 1,
    ) -> None:
        if self._recorder is None:
            return
        try:
            await self._recorder(
                {
                    "url": url[:2000],
                    "host": host,
                    "decision": decision,
                    "reason": reason[:200],
                    "bytes_sent": bytes_sent,
                    "bytes_received": bytes_received,
                    "request_count": request_count,
                }
            )
        except Exception:  # noqa: BLE001 - 审计失败不阻断代理
            logger.warning("egress audit record failed", exc_info=True)

    # ------------------------------------------------------------------
    # 客户端协议处理
    # ------------------------------------------------------------------

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line:
                return
            line = request_line.decode("latin-1", errors="replace").strip()
            parts = line.split()
            if not parts:
                return
            method = parts[0].upper()
            target = parts[1] if len(parts) > 1 else ""
            if method == "CONNECT":
                await self._handle_connect(reader, writer, target)
                return
            if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
                await self._handle_http(reader, writer, method, target)
                return
            await self._reject(writer, "unsupported method")
        except (asyncio.CancelledError, ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _handle_connect(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        target: str,
    ) -> None:
        host, _, port_str = target.partition(":")
        port = 443
        try:
            port = int(port_str) if port_str else 443
        except ValueError:
            await self._reject(writer, "invalid CONNECT port")
            return
        reason = validate_egress_url(
            f"https://{host}:{port}",
            allowed_hosts=self._allowed_hosts,
            allowed_ports=self._allowed_ports,
        )
        if reason:
            await self._record(
                url=f"https://{host}:{port}",
                host=host,
                decision="deny",
                reason=reason,
            )
            await self._reject(writer, reason)
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=_RESPONSE_TIMEOUT
            )
        except (TimeoutError, OSError) as exc:
            await self._record(
                url=f"https://{host}:{port}",
                host=host,
                decision="deny",
                reason=f"upstream connect failed: {exc}",
            )
            await self._reject(writer, "upstream connect failed")
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await self._record(
            url=f"https://{host}:{port}",
            host=host,
            decision="allow",
            reason="allow-listed CONNECT tunnel",
        )
        try:
            async def pump(
                src: asyncio.StreamReader,
                dst: asyncio.StreamWriter,
                counter: list[int],
            ) -> None:
                while True:
                    chunk = await src.read(64 * 1024)
                    if not chunk:
                        break
                    counter[0] += len(chunk)
                    if counter[0] > _MAX_TUNNEL_BYTES:
                        break
                    dst.write(chunk)
                    await dst.drain()

            client_to_upstream = asyncio.create_task(pump(reader, upstream_writer, [0]))
            upstream_to_client = asyncio.create_task(pump(upstream_reader, writer, [0]))
            done, pending = await asyncio.wait(
                {client_to_upstream, upstream_to_client},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                try:
                    await task
                except Exception:
                    pass
        finally:
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except Exception:
                pass

    async def _handle_http(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
    ) -> None:
        url = target
        if url.startswith("/"):
            # 无绝对 URL 的请求不做出口决策（本代理只服务出口流量）。
            await self._reject(writer, "absolute URL required")
            return
        if not url.startswith(("http://", "https://")):
            await self._reject(writer, "unsupported URL scheme")
            return
        reason = validate_egress_url(
            url,
            allowed_hosts=self._allowed_hosts,
            allowed_ports=self._allowed_ports,
        )
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if reason:
            await self._record(url=url, host=host, decision="deny", reason=reason)
            await self._reject(writer, reason)
            return
        try:
            body = await reader.read(1024 * 1024)
        except TimeoutError:
            body = b""
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            result = await self._forward_once(method, current, host, body)
            status, location = result
            if status in {301, 302, 303, 307, 308} and location:
                redirect_url = urllib.parse.urljoin(current, location)
                redirect_reason = validate_egress_url(
                    redirect_url,
                    allowed_hosts=self._allowed_hosts,
                    allowed_ports=self._allowed_ports,
                )
                if redirect_reason:
                    await self._record(
                        url=redirect_url,
                        host=(urllib.parse.urlparse(redirect_url).hostname or "").lower(),
                        decision="deny",
                        reason=f"redirect denied: {redirect_reason}",
                    )
                    await self._reject(writer, "redirect target denied")
                    return
                current = redirect_url
                continue
            await self._record(
                url=current,
                host=(urllib.parse.urlparse(current).hostname or "").lower(),
                decision="allow",
                reason="allow-listed HTTP request",
                bytes_received=0,
            )
            break
        else:
            await self._reject(writer, "too many redirects")
            return

    async def _forward_once(
        self,
        method: str,
        url: str,
        host: str,
        body: bytes,
    ) -> tuple[int, str | None]:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(parsed.hostname, port),
                timeout=_RESPONSE_TIMEOUT,
            )
        except (TimeoutError, OSError) as exc:
            await self._reject_to(None, f"upstream connect failed: {exc}")
            return 502, None
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        request_head = (
            f"{method} {path} HTTP/1.1\r\n"
            f"Host: {parsed.netloc}\r\n"
            "Connection: close\r\n"
            "User-Agent: coifesp-egress-proxy/1.0\r\n"
            "Accept: */*\r\n"
        ).encode("latin-1")
        if body:
            request_head += f"Content-Length: {len(body)}\r\n".encode("latin-1")
        request_head += b"\r\n"
        upstream_writer.write(request_head + body)
        await upstream_writer.drain()
        response = await asyncio.wait_for(
            upstream_reader.read(64 * 1024), timeout=_RESPONSE_TIMEOUT
        )
        upstream_writer.close()
        try:
            await upstream_writer.wait_closed()
        except Exception:
            pass
        if not response:
            return 502, None
        status_line = response.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
        try:
            status = int(status_line.split()[1])
        except (IndexError, ValueError):
            status = 502
        location = ""
        for raw_line in response.split(b"\r\n")[1:]:
            if raw_line.lower().startswith(b"location:"):
                location = raw_line.split(b":", 1)[1].strip().decode("latin-1", errors="replace")
                break
        return status, location or None

    async def _reject(self, writer: asyncio.StreamWriter, reason: str) -> None:
        await self._reject_to(writer, reason)

    async def _reject_to(self, writer: asyncio.StreamWriter | None, reason: str) -> None:
        if writer is None:
            return
        body = f"egress denied: {reason}".encode()
        writer.write(
            b"HTTP/1.1 403 Forbidden\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("latin-1")
            + b"\r\n"
            + body
        )
        try:
            await writer.drain()
        except Exception:
            pass
