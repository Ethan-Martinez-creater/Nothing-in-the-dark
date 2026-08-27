"""M9: read-only MCP server, client allow-list and unified tool registration.

Covers:
- the four read-only MCP server tools over an in-memory transport;
- client allow-list rejection of unknown servers;
- the readonly policy (write-marked tools filtered/rejected);
- timeout / cancellation / result normalization;
- registering discovered MCP tools as `mcp:{server}:{tool}` ToolSpecs that
  flow through the normal permission / cache / audit path.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.harness.tool_factory import register_mcp_tools
from app.harness.tools import ToolRegistry
from app.infrastructure.database import Database
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.mcp.client import McpClientManager, McpServerConfig, is_write_tool
from app.mcp.server import build_readonly_mcp_server
from app.schemas.cases import CreateCaseRequest
from app.schemas.knowledge import CreateMemoryRequest

_READONLY_TOOLS = {
    "search_social_evidence",
    "get_case_summary",
    "get_artifact",
    "get_propagation_graph",
}

_POSTS = [
    {
        "platform": "weibo",
        "native_id": "wb-1",
        "content": "有人宣称某地发生谣言事件",
        "author": "用户甲",
        "url": "https://weibo.com/u/1",
        "published_at": "2026-08-01T10:00:00Z",
        "metrics": {},
        "raw": {},
    },
    {
        "platform": "bilibili",
        "native_id": "bili-1",
        "content": "回复：该说法没有证据支持",
        "author": "用户乙",
        "url": "https://bilibili.com/video/1",
        "published_at": "2026-08-01T11:00:00Z",
        "metrics": {},
        "raw": {},
    },
]


@pytest.fixture
async def repos(tmp_path: Path) -> tuple[
    Database,
    ApplicationRepository,
    KnowledgeRepository,
    SocialRepository,
]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    knowledge = KnowledgeRepository(database)
    social = SocialRepository(database)
    yield database, repository, knowledge, social
    await database.dispose()


@pytest.fixture
async def seeded(repos: tuple[
    Database,
    ApplicationRepository,
    KnowledgeRepository,
    SocialRepository,
]) -> tuple[
    ApplicationRepository,
    KnowledgeRepository,
    SocialRepository,
    dict[str, str],
]:
    _, repository, knowledge, social = repos
    case = await repository.create_case(
        CreateCaseRequest(
            topic="谣言事件调查",
            description="调查一个谣言案例",
            platforms=["weibo", "bilibili"],
            time_start="2026-08-01T00:00:00Z",
            time_end="2026-08-02T00:00:00Z",
        )
    )
    result = await social.persist_batch(case_id=case.id, posts=_POSTS)
    assert result.posts_created == 2
    posts = await social.list_posts_by_case(case.id)
    artifact = await repository.create_artifact(
        case_id=case.id,
        kind="opinion",
        title="观点分析 v1",
        data={"summary": "初版"},
    )
    await repository.create_artifact(
        case_id=case.id,
        kind="opinion",
        title="观点分析 v2",
        data={"summary": "修订版"},
    )
    claim = await repository.create_claim(
        case_id=case.id,
        text="某地发生谣言事件",
        created_by_run_id="run-1",
    )
    await repository.create_evidence(
        case_id=case.id,
        claim_id=claim.id,
        source_type="post",
        source_id=posts[0].id,
        stance="support",
        excerpt=posts[0].content,
        relevance=0.9,
    )
    await repository.create_propagation_edge(
        case_id=case.id,
        source_post_id=posts[0].id,
        target_post_id=posts[1].id,
        relation="inferred",
        confidence=0.6,
        feature_scores={"similarity": 0.6},
        evidence_ids=[claim.id],
        algorithm_version="1.1.0",
    )
    await knowledge.create_memory(
        case.id,
        CreateMemoryRequest(
            kind="fact",
            content="用户确认该事件无可靠信源",
            importance=0.8,
            source_type="user",
            source_id="user-1",
        ),
    )
    ids = {
        "case_id": case.id,
        "artifact_id": artifact.id,
        "claim_id": claim.id,
        "post_a": posts[0].id,
        "post_b": posts[1].id,
    }
    return repository, knowledge, social, ids


# ----------------------------------------------------------------------
# Server: tool surface
# ----------------------------------------------------------------------


async def test_server_exposes_only_readonly_tools(repos: tuple[
    Database,
    ApplicationRepository,
    KnowledgeRepository,
    SocialRepository,
]) -> None:
    _, repository, knowledge, social = repos
    server = build_readonly_mcp_server(
        repository=repository,
        knowledge=knowledge,
        social=social,
    )
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        tools = await session.list_tools()
    assert {tool.name for tool in tools.tools} == _READONLY_TOOLS


# ----------------------------------------------------------------------
# Server: the four tools
# ----------------------------------------------------------------------


async def test_search_social_evidence_hits(seeded: Any) -> None:
    repository, knowledge, social, ids = seeded
    server = build_readonly_mcp_server(
        repository=repository,
        knowledge=knowledge,
        social=social,
    )
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        result = await session.call_tool(
            "search_social_evidence",
            {"case_id": ids["case_id"], "query": "谣言"},
        )
        payload = _json_payload(result)
    assert payload["ok"] is True
    assert payload["case_id"] == ids["case_id"]
    assert len(payload["hits"]) >= 1
    hit = payload["hits"][0]
    assert hit["evidence_id"]
    assert hit["source_type"] in {
        "post",
        "memory",
        "claim",
        "evidence",
        "comment",
        "artifact",
        "document_chunk",
    }
    assert "谣言" in hit["content"]


async def test_search_social_evidence_invalid_time_range(seeded: Any) -> None:
    repository, knowledge, social, ids = seeded
    server = build_readonly_mcp_server(
        repository=repository,
        knowledge=knowledge,
        social=social,
    )
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        result = await session.call_tool(
            "search_social_evidence",
            {
                "case_id": ids["case_id"],
                "query": "谣言",
                "time_range": {"from": "not-a-date"},
            },
        )
        payload = _json_payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_time_range"


async def test_get_case_summary(seeded: Any) -> None:
    repository, knowledge, social, ids = seeded
    server = build_readonly_mcp_server(
        repository=repository,
        knowledge=knowledge,
        social=social,
    )
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        result = await session.call_tool(
            "get_case_summary",
            {"case_id": ids["case_id"]},
        )
        payload = _json_payload(result)
    assert payload["ok"] is True
    assert payload["case"]["topic"] == "谣言事件调查"
    stats = payload["stats"]
    assert stats["posts"] == 2
    assert stats["artifacts"] == 2
    assert stats["claims"] == 1
    assert stats["evidence"] == 1
    assert stats["propagation_edges"] == 1
    assert stats["artifact_kinds"] == ["opinion"]


async def test_get_artifact_with_versions(seeded: Any) -> None:
    repository, knowledge, social, ids = seeded
    server = build_readonly_mcp_server(
        repository=repository,
        knowledge=knowledge,
        social=social,
    )
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        result = await session.call_tool(
            "get_artifact",
            {"artifact_id": ids["artifact_id"]},
        )
        payload = _json_payload(result)
    assert payload["ok"] is True
    assert payload["artifact"]["artifact_id"] == ids["artifact_id"]
    assert payload["artifact"]["version"] == 1
    assert [v["version"] for v in payload["versions"]] == [1, 2]
    assert payload["artifact"]["data"]["summary"] == "初版"


async def test_get_propagation_graph(seeded: Any) -> None:
    repository, knowledge, social, ids = seeded
    server = build_readonly_mcp_server(
        repository=repository,
        knowledge=knowledge,
        social=social,
    )
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        result = await session.call_tool(
            "get_propagation_graph",
            {"case_id": ids["case_id"]},
        )
        payload = _json_payload(result)
    assert payload["ok"] is True
    assert payload["node_count"] == 2
    assert payload["edge_count"] == 1
    edge = payload["edges"][0]
    assert edge["relation"] == "inferred"
    assert edge["confidence"] == 0.6
    assert edge["source_post_id"] == ids["post_a"]
    assert edge["target_post_id"] == ids["post_b"]
    assert {node["platform"] for node in payload["nodes"]} == {
        "weibo",
        "bilibili",
    }


async def test_missing_case_returns_not_found(seeded: Any) -> None:
    repository, knowledge, social, _ = seeded
    server = build_readonly_mcp_server(
        repository=repository,
        knowledge=knowledge,
        social=social,
    )
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        for tool, arguments in [
            ("get_case_summary", {"case_id": "missing-case"}),
            ("get_artifact", {"artifact_id": "missing-artifact"}),
            ("get_propagation_graph", {"case_id": "missing-case"}),
        ]:
            result = await session.call_tool(tool, arguments)
            payload = _json_payload(result)
            assert payload["ok"] is False, tool
            assert payload["found"] is False, tool


# ----------------------------------------------------------------------
# Client: allow-list and readonly policy
# ----------------------------------------------------------------------


async def test_unknown_server_rejected() -> None:
    manager = McpClientManager([])
    with pytest.raises(ApplicationError) as exc:
        await manager.discover_tools("evil-server")
    assert exc.value.code == "mcp_server_unknown"
    with pytest.raises(ApplicationError) as exc:
        await manager.call_tool("evil-server", "read_something", {})
    assert exc.value.code == "mcp_server_unknown"
    assert manager.names() == []


def test_write_marker_detection() -> None:
    assert is_write_tool("write_case_memory")
    assert is_write_tool("delete_artifact")
    assert is_write_tool("approve_crawl")
    assert is_write_tool("database_update")
    assert not is_write_tool("search_social_evidence")
    assert not is_write_tool("get_artifact")
    assert not is_write_tool("list_things")


async def test_readonly_call_refuses_write_tool() -> None:
    manager = McpClientManager(
        [
            McpServerConfig(
                name="trusted",
                command="python",
                args=["-c", "pass"],
            )
        ]
    )
    with pytest.raises(ApplicationError) as exc:
        await manager.call_tool("trusted", "delete_everything", {})
    assert "readonly" in exc.value.message
    assert exc.value.code == "mcp_tool_error"


class _EchoServer:
    """A tiny stand-in MCP server used for client-side transport tests."""

    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("echo-server")

        @mcp.tool()
        async def echo(text: str, number: int = 1) -> dict[str, Any]:
            if self.delay:
                await asyncio.sleep(self.delay)
            return {"text": text, "number": number, "server": "echo"}

        @mcp.tool()
        async def delete_something() -> str:  # write-marked, filtered
            return "nope"

        self.mcp = mcp


@asynccontextmanager
async def _session(server: Any):
    """In-memory client session bound to the caller's own task.

    The session must be entered and exited from the same asyncio task
    (anyio cancel-scope requirement), so tests open it inline instead of
    through a fixture.
    """
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        yield session


async def test_discovery_filters_write_tools() -> None:
    async with _session(_EchoServer().mcp) as session:
        manager = McpClientManager(
            [McpServerConfig(name="echo", command="x", args=[])]
        )
        manager._sessions["echo"] = session  # inject in-memory session
        tools = await manager.discover_tools("echo")
        names = [tool.name for tool in tools]
        assert "echo" in names
        assert "delete_something" not in names  # readonly policy dropped it
        # second call hits the cache without reconnecting
        assert await manager.discover_tools("echo") is tools


async def test_call_tool_normalizes_result() -> None:
    async with _session(_EchoServer().mcp) as session:
        manager = McpClientManager(
            [McpServerConfig(name="echo", command="x", args=[])]
        )
        manager._sessions["echo"] = session
        result = await manager.call_tool(
            "echo",
            "echo",
            {"text": "你好", "number": 3},
        )
        assert result["ok"] is True
        assert result["server"] == "echo"
        assert result["tool"] == "echo"
        assert result["data"] == {"text": "你好", "number": 3, "server": "echo"}
        assert any(item["type"] == "text" for item in result["content"])


async def test_call_tool_timeout() -> None:
    slow = _EchoServer(delay=2.0)
    async with create_connected_server_and_client_session(slow.mcp) as session:
        await session.initialize()
        manager = McpClientManager(
            [McpServerConfig(name="slow", command="x", timeout_seconds=0.2)]
        )
        manager._sessions["slow"] = session
        with pytest.raises(ApplicationError) as exc:
            await manager.call_tool("slow", "echo", {"text": "hi"})
    assert exc.value.code == "mcp_timeout"


async def test_call_tool_cancelled() -> None:
    async with _session(_EchoServer().mcp) as session:
        manager = McpClientManager(
            [McpServerConfig(name="echo", command="x", args=[])]
        )
        manager._sessions["echo"] = session
        cancel_event = asyncio.Event()
        cancel_event.set()
        with pytest.raises(ApplicationError) as exc:
            await manager.call_tool(
                "echo", "echo", {"text": "hi"}, cancel_event=cancel_event
            )
        assert exc.value.code == "tool_cancelled"


async def test_call_tool_undiscovered_rejected() -> None:
    async with _session(_EchoServer().mcp) as session:
        manager = McpClientManager(
            [McpServerConfig(name="echo", command="x", args=[])]
        )
        manager._sessions["echo"] = session
        with pytest.raises(ApplicationError) as exc:
            await manager.call_tool("echo", "no_such_tool", {})
        assert exc.value.code == "mcp_tool_error"


async def test_close_clears_state() -> None:
    async with _session(_EchoServer().mcp) as session:
        manager = McpClientManager(
            [McpServerConfig(name="echo", command="x", args=[])]
        )
        manager._sessions["echo"] = session
        await manager.close()
        assert manager._sessions == {}
        assert manager._discovered == {}


# ----------------------------------------------------------------------
# Unified registration: MCP tools become regular ToolSpecs
# ----------------------------------------------------------------------


async def test_register_mcp_tools_and_invoke() -> None:
    async with _session(_EchoServer().mcp) as session:
        manager = McpClientManager(
            [McpServerConfig(name="echo", command="x", args=[])]
        )
        manager._sessions["echo"] = session
        registry = ToolRegistry()
        registered = await register_mcp_tools(registry, manager)
        assert registered == ["mcp:echo:echo"]
        assert "mcp:echo:echo" in registry.names()
        spec = registry.get("mcp:echo:echo")
        assert spec.side_effect == "none"
        assert spec.idempotent is True
        assert spec.permissions == ("read_database",)

        invocation = await registry.invoke_with_meta(
            "mcp:echo:echo",
            {"text": "你好", "number": 2},
            granted_permissions={"read_database"},
        )
        assert invocation.output["ok"] is True
        assert invocation.output["data"]["text"] == "你好"
        assert invocation.output["data"]["number"] == 2

        # second call within the TTL hits the result cache
        cached = await registry.invoke_with_meta(
            "mcp:echo:echo",
            {"text": "你好", "number": 2},
            granted_permissions={"read_database"},
        )
        assert cached.cached is True


async def test_register_mcp_tools_permission_enforced() -> None:
    async with _session(_EchoServer().mcp) as session:
        manager = McpClientManager(
            [McpServerConfig(name="echo", command="x", args=[])]
        )
        manager._sessions["echo"] = session
        registry = ToolRegistry()
        await register_mcp_tools(registry, manager)
        with pytest.raises(ApplicationError) as exc:
            await registry.invoke(
                "mcp:echo:echo",
                {"text": "hi"},
                granted_permissions=set(),
            )
        assert exc.value.code == "tool_permission_denied"


async def test_register_mcp_tools_skips_unknown_server() -> None:
    manager = McpClientManager(
        [McpServerConfig(name="echo", command="x", args=[])]
    )
    registry = ToolRegistry()
    # a server name outside the allow-list is skipped with a warning
    registered = await register_mcp_tools(registry, manager, server_names=["ghost"])
    assert registered == []
    assert registry.names() == set()


def _json_payload(result: Any) -> dict[str, Any]:
    """Extract the JSON payload from a CallToolResult's text content."""
    text = ""
    for item in result.content:
        if getattr(item, "type", "") == "text":
            text += item.text
    assert text, "tool returned no text content"
    import json

    return json.loads(text)
