"""M9: MCP client manager with an explicit server allow-list.

Transport support:
- ``stdio`` — spawn a server process and speak JSON-RPC over its stdin/stdout;
- ``streamable_http`` — connect to a remote MCP endpoint.

Safety policy:
- Only servers explicitly configured in ``McpServerConfig`` are ever
  contacted; connecting to an unknown name raises ``mcp_server_unknown``.
- Every server is ``readonly`` by default: tools whose names match the
  write-operation blacklist are filtered out during discovery, and all
  registered tools are treated as side-effect-free reads (M9 scope).
- Calls carry a timeout; run-scoped cancellation is propagated to the
  underlying session.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.errors import ApplicationError

logger = logging.getLogger(__name__)

#: Write-operation name markers rejected under the readonly policy.
_WRITE_OP_MARKERS = (
    "write",
    "delete",
    "update",
    "create",
    "publish",
    "approve",
    "cancel",
    "submit",
    "insert",
    "remove",
    "modify",
    "patch",
    "put",
    "post",
)


class McpServerConfig(BaseModel):
    """One entry in the MCP server allow-list."""

    name: str
    transport: Literal["stdio", "streamable_http"] = "stdio"
    # stdio transport
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # streamable_http transport
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 30.0
    enabled: bool = True
    # M9: external MCP servers are treated as read-only until write
    # operations go through the approval pipeline.
    readonly: bool = True


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    """Discovered MCP tool metadata (from ``tools/list``)."""

    name: str
    description: str
    input_schema: dict[str, Any]


def is_write_tool(name: str) -> bool:
    """True when a tool name suggests a write/delete-style operation.

    Used by the readonly policy to refuse registering such tools from
    external MCP servers in M9 scope.
    """
    lowered = name.lower()
    return any(marker in lowered for marker in _WRITE_OP_MARKERS)


def _tool_error(server: str, tool: str, message: str) -> ApplicationError:
    return ApplicationError(
        f"MCP server '{server}' tool '{tool}': {message}",
        code="mcp_tool_error",
    )


class McpClientManager:
    """Lifecycle manager for allow-listed MCP server connections."""

    def __init__(self, servers: list[McpServerConfig] | None = None) -> None:
        self._configs = {
            config.name: config
            for config in (servers or [])
            if config.enabled
        }
        self._sessions: dict[str, Any] = {}
        self._stacks: dict[str, AsyncExitStack] = {}
        self._discovered: dict[str, list[McpToolDescriptor]] = {}

    # ------------------------------------------------------------------
    # Allow-list
    # ------------------------------------------------------------------

    def names(self) -> list[str]:
        return sorted(self._configs)

    def is_configured(self, name: str) -> bool:
        return name in self._configs

    def config(self, name: str) -> McpServerConfig:
        if name not in self._configs:
            raise ApplicationError(
                f"MCP server '{name}' is not configured (allow-list rejected)",
                code="mcp_server_unknown",
            )
        return self._configs[name]

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def _open_session(
        self,
        config: McpServerConfig,
    ) -> tuple[AsyncExitStack, Any]:
        """Open a transport + session for one server.

        Returns ``(exit_stack, ClientSession)``; the caller owns the stack.
        """
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamablehttp_client

        stack = AsyncExitStack()
        if config.transport == "stdio":
            if not config.command:
                raise ApplicationError(
                    f"MCP server '{config.name}': stdio transport requires a command",
                    code="mcp_invalid_config",
                )
            params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=config.env or None,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(
                ClientSession(read, write)
            )
        else:
            if not config.url:
                raise ApplicationError(
                    f"MCP server '{config.name}': streamable_http requires a url",
                    code="mcp_invalid_config",
                )
            read, write = await stack.enter_async_context(
                streamablehttp_client(config.url, headers=config.headers or None)
            )
            session = await stack.enter_async_context(
                ClientSession(read, write)
            )
        await session.initialize()
        return stack, session

    async def _ensure_session(self, name: str) -> Any:
        config = self.config(name)
        if name not in self._sessions:
            stack, session = await self._open_session(config)
            self._sessions[name] = session
            self._stacks[name] = stack
        return self._sessions[name]

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def discover_tools(
        self,
        name: str,
        *,
        force: bool = False,
    ) -> list[McpToolDescriptor]:
        """List tools exposed by a server (cached after first connect).

        Under the readonly policy, write-marked tools are filtered out.
        """
        config = self.config(name)
        if not force and name in self._discovered:
            return self._discovered[name]
        session = await self._ensure_session(name)
        try:
            result = await asyncio.wait_for(
                session.list_tools(),
                timeout=config.timeout_seconds,
            )
        except TimeoutError as exc:
            raise ApplicationError(
                f"MCP server '{name}' tool discovery timed out",
                code="mcp_timeout",
            ) from exc
        descriptors = [
            McpToolDescriptor(
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.inputSchema or {}),
            )
            for tool in result.tools
        ]
        if config.readonly:
            filtered = [d for d in descriptors if not is_write_tool(d.name)]
            dropped = [d.name for d in descriptors if is_write_tool(d.name)]
            if dropped:
                logger.warning(
                    "MCP server '%s' readonly policy dropped write-marked tools: %s",
                    name,
                    ", ".join(sorted(dropped)),
                )
            descriptors = filtered
        self._discovered[name] = descriptors
        return descriptors

    # ------------------------------------------------------------------
    # Tool calls
    # ------------------------------------------------------------------

    async def call_tool(
        self,
        name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        """Invoke one discovered tool on an allow-listed server.

        Unknown servers and undiscovered tools are rejected before any
        network I/O happens. Timeouts and run-scoped cancellation map to
        the existing tool error codes so the audit trail stays uniform.
        """
        config = self.config(name)
        if config.readonly and is_write_tool(tool_name):
            raise _tool_error(
                name,
                tool_name,
                "refused by the readonly policy (write-marked tool)",
            )
        discovered = await self.discover_tools(name)
        if not any(tool.name == tool_name for tool in discovered):
            raise _tool_error(name, tool_name, "tool not discovered")
        session = await self._ensure_session(name)
        payload = arguments if arguments is not None else {}
        if cancel_event is not None:
            if cancel_event.is_set():
                raise ApplicationError(
                    f"MCP tool '{name}:{tool_name}' was cancelled",
                    code="tool_cancelled",
                )
            cancel_listener = asyncio.create_task(cancel_event.wait())
        else:
            cancel_listener = None
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool_name, payload),
                timeout=config.timeout_seconds,
            )
        except ApplicationError:
            raise
        except TimeoutError as exc:
            raise ApplicationError(
                f"MCP server '{name}' tool '{tool_name}' timed out",
                code="mcp_timeout",
            ) from exc
        except Exception as exc:
            raise _tool_error(name, tool_name, str(exc)[:500]) from exc
        finally:
            if cancel_listener is not None:
                cancel_listener.cancel()
        cancel_fired = (
            cancel_listener is not None
            and cancel_listener.done()
            and not cancel_listener.cancelled()
        )
        if cancel_fired:
            raise ApplicationError(
                f"MCP tool '{name}:{tool_name}' was cancelled",
                code="tool_cancelled",
            )
        return _normalize_result(name, tool_name, result)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def close(self) -> None:
        for stack in self._stacks.values():
            try:
                await stack.aclose()
            except Exception:  # pragma: no cover - best effort shutdown
                logger.exception("Error closing MCP transport")
        self._sessions.clear()
        self._stacks.clear()
        self._discovered.clear()


def _normalize_result(
    server: str,
    tool: str,
    result: Any,
) -> dict[str, Any]:
    """Shape an MCP ``CallToolResult`` into the registry's output contract.

    Structured ``content`` items are kept verbatim; if the first text item
    parses as JSON it is also surfaced as ``data`` for downstream tools.
    """
    content: list[dict[str, Any]] = []
    if getattr(result, "content", None):
        for item in result.content:
            content.append(
                {
                    "type": getattr(item, "type", "text"),
                    "text": getattr(item, "text", "") or "",
                }
            )
    structured: dict[str, Any] | None = None
    for item in content:
        if item["type"] == "text" and item["text"]:
            try:
                parsed = json.loads(item["text"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict):
                structured = parsed
            break
    if getattr(result, "isError", False):
        return {
            "ok": False,
            "server": server,
            "tool": tool,
            "error": {
                "code": "mcp_tool_error",
                "message": content[0]["text"] if content else "MCP tool failed",
            },
        }
    return {
        "ok": True,
        "server": server,
        "tool": tool,
        "content": content,
        "data": structured,
    }
