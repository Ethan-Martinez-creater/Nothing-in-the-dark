"""M9 smoke: read-only MCP server and client allow-list against a real DB.

Usage:
    python -m scripts.smoke_mcp_server

Verifies, on a temporary SQLite database:
* the server exposes exactly the four read-only tools;
* search_social_evidence returns stable evidence IDs;
* get_case_summary aggregates posts/artifacts/claims/evidence/edges;
* get_artifact returns the artifact and its version history;
* get_propagation_graph returns posts (nodes) and edges;
* missing cases report found=false instead of failing;
* the client allow-list rejects unknown servers before any I/O.

Exit code 0 on success, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from mcp.shared.memory import create_connected_server_and_client_session

from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.infrastructure.database import Database
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.mcp.client import McpClientManager
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
        "content": "某地谣言事件引发关注",
        "author": "用户甲",
        "url": "https://weibo.com/u/1",
        "published_at": "2026-08-01T10:00:00Z",
        "metrics": {},
        "raw": {},
    },
    {
        "platform": "bilibili",
        "native_id": "bili-1",
        "content": "回复：该说法缺乏证据",
        "author": "用户乙",
        "url": "https://bilibili.com/video/1",
        "published_at": "2026-08-01T11:00:00Z",
        "metrics": {},
        "raw": {},
    },
]


async def _main() -> int:
    checks = 0
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "mcp_smoke.db"
        database = Database(f"sqlite+aiosqlite:///{db_path}")
        await database.create_schema()
        repository = ApplicationRepository(database)
        knowledge = KnowledgeRepository(database)
        social = SocialRepository(database)

        case = await repository.create_case(
            CreateCaseRequest(
                topic="谣言事件调查",
                description="冒烟案例",
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
            text="某地谣言事件引发关注",
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
                source_type="user",
                source_id="user-1",
            ),
        )

        server = build_readonly_mcp_server(
            repository=repository,
            knowledge=knowledge,
            social=social,
        )
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert names == _READONLY_TOOLS, f"unexpected tool surface: {names}"
            checks += 1
            print(f"[1/7] tool surface OK: {sorted(names)}")

            def payload(result: object) -> dict:
                text = ""
                for item in result.content:  # type: ignore[attr-defined]
                    if getattr(item, "type", "") == "text":
                        text += item.text
                assert text, "empty tool response"
                return json.loads(text)

            result = await session.call_tool(
                "search_social_evidence",
                {"case_id": case.id, "query": "谣言"},
            )
            data = payload(result)
            assert data["ok"] is True and data["hits"], "no hits returned"
            assert data["hits"][0]["evidence_id"], "hits lack evidence ids"
            checks += 1
            print(f"[2/7] search_social_evidence OK: {len(data['hits'])} hits")

            result = await session.call_tool(
                "get_case_summary",
                {"case_id": case.id},
            )
            data = payload(result)
            stats = data["stats"]
            assert data["found"] is True
            assert stats["posts"] == 2 and stats["artifacts"] == 2
            assert stats["claims"] == 1 and stats["evidence"] == 1
            assert stats["propagation_edges"] == 1
            checks += 1
            print("[3/7] get_case_summary OK:", stats)

            result = await session.call_tool(
                "get_artifact",
                {"artifact_id": artifact.id},
            )
            data = payload(result)
            assert data["found"] is True
            assert data["artifact"]["version"] == 1
            assert [v["version"] for v in data["versions"]] == [1, 2]
            checks += 1
            print("[4/7] get_artifact OK: 2 versions")

            result = await session.call_tool(
                "get_propagation_graph",
                {"case_id": case.id},
            )
            data = payload(result)
            assert data["node_count"] == 2 and data["edge_count"] == 1
            assert data["edges"][0]["relation"] == "inferred"
            checks += 1
            print("[5/7] get_propagation_graph OK: 2 nodes, 1 edge")

            for tool, arguments in [
                ("get_case_summary", {"case_id": "missing-case"}),
                ("get_artifact", {"artifact_id": "missing-artifact"}),
            ]:
                data = payload(await session.call_tool(tool, arguments))
                assert data["ok"] is False and data["found"] is False
            checks += 1
            print("[6/7] missing-case handling OK")

        # Client allow-list: unknown servers are rejected before any I/O.
        manager = McpClientManager([])
        try:
            await manager.discover_tools("evil-server")
        except ApplicationError as exc:
            assert exc.code == "mcp_server_unknown", exc.code
        else:
            raise AssertionError("unknown server was not rejected")
        checks += 1
        print("[7/7] client allow-list OK: unknown server rejected")

        await database.dispose()

    print(f"SMOKE OK ({checks}/7 checks)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
