"""M9: the read-only MCP server.

Exposes four case-scoped, side-effect-free tools over FastMCP:

- ``search_social_evidence`` — mixed retrieval over posts, comments,
  documents, memory, claims and evidence (same pipeline as the local
  ``search_social_evidence`` tool);
- ``get_case_summary`` — case metadata plus aggregate counts;
- ``get_artifact`` — one artifact (by id) including its versions;
- ``get_propagation_graph`` — posts as nodes plus persisted edges.

The server only reads; there is no write tool registered anywhere in the
MCP surface (14.2: crawlers, database writes and configuration stay inside
the harness).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.embeddings import EmbeddingWorkerClient

_DEFAULT_LIMIT = 12
_MAX_LIMIT = 100


def build_readonly_mcp_server(
    *,
    repository: ApplicationRepository,
    knowledge: KnowledgeRepository | None = None,
    social: SocialRepository | None = None,
    embeddings: EmbeddingWorkerClient | None = None,
) -> Any:
    """Assemble a FastMCP server bound to the given repositories.

    Returns the FastMCP instance; callers run it via ``mcp.run()``
    (stdio) or mount ``mcp.streamable_http_app()`` in a web framework.
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("coifesp-readonly")

    # ------------------------------------------------------------------
    # Tool 1: search_social_evidence
    # ------------------------------------------------------------------

    @mcp.tool()
    async def search_social_evidence(
        case_id: str,
        query: str,
        limit: int = _DEFAULT_LIMIT,
        platforms: list[str] | None = None,
        time_range: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        """Search case-scoped social posts, documents, memory, claims and
        evidence, returning stable evidence IDs for citation."""
        if knowledge is None:
            return {
                "ok": False,
                "error": {"code": "unavailable", "message": "Knowledge store unavailable"},
            }
        effective_limit = max(1, min(limit, _MAX_LIMIT))
        query_vectors = (
            await embeddings.embed([query]) if embeddings is not None else None
        )
        time_from = time_to = None
        if time_range:
            try:
                if time_range.get("from"):
                    time_from = datetime.fromisoformat(time_range["from"])
                if time_range.get("to"):
                    time_to = datetime.fromisoformat(time_range["to"])
            except ValueError:
                return {
                    "ok": False,
                    "error": {
                        "code": "invalid_time_range",
                        "message": "time_range must contain ISO-8601 dates.",
                    },
                }
        hits = await knowledge.search(
            case_id=case_id,
            query=query,
            limit=effective_limit,
            embedding=query_vectors[0] if query_vectors else None,
            platforms=platforms,
            time_from=time_from,
            time_to=time_to,
        )
        return {
            "ok": True,
            "available": True,
            "case_id": case_id,
            "hits": [
                {
                    "evidence_id": hit.evidence_id,
                    "source_type": hit.source_type,
                    "source_id": hit.source_id,
                    "content": hit.content,
                    "score": hit.score,
                    "retrieval_modes": hit.retrieval_modes,
                    "platform": hit.platform,
                    "source_url": hit.source_url,
                    "published_at": (
                        hit.published_at.isoformat() if hit.published_at else None
                    ),
                }
                for hit in hits
            ],
        }

    # ------------------------------------------------------------------
    # Tool 2: get_case_summary
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_case_summary(case_id: str) -> dict[str, Any]:
        """Return a case's metadata and aggregate analysis counts."""
        try:
            case = await repository.get_case(case_id)
        except ApplicationError:
            return {"ok": False, "found": False, "case_id": case_id}
        turns = await repository.list_turns(case_id)
        artifacts = await repository.list_artifacts(case_id)
        claims = await repository.list_claims_by_case(case_id)
        evidence = await repository.list_evidence_by_case(case_id)
        edges = await repository.list_propagation_edges_by_case(case_id)
        posts = await social.list_posts_by_case(case_id) if social is not None else []
        return {
            "ok": True,
            "found": True,
            "case": {
                "case_id": case.id,
                "title": case.title,
                "topic": case.topic,
                "description": case.description,
                "status": case.status,
                "platforms": case.platforms,
                "time_range": case.time_range,
                "created_at": case.created_at.isoformat() if case.created_at else None,
            },
            "stats": {
                "posts": len(posts),
                "turns": len(turns),
                "artifacts": len(artifacts),
                "claims": len(claims),
                "evidence": len(evidence),
                "propagation_edges": len(edges),
                "artifact_kinds": sorted({a.kind for a in artifacts}),
            },
        }

    # ------------------------------------------------------------------
    # Tool 3: get_artifact
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_artifact(artifact_id: str) -> dict[str, Any]:
        """Return one artifact and its version history."""
        try:
            record = await repository.get_artifact(artifact_id)
        except ApplicationError:
            return {"ok": False, "found": False, "artifact_id": artifact_id}
        versions = await repository.list_artifact_versions(record.id)
        return {
            "ok": True,
            "found": True,
            "artifact": {
                "artifact_id": record.id,
                "case_id": record.case_id,
                "kind": record.kind,
                "version": record.version,
                "title": record.title,
                "run_id": record.run_id,
                "data": record.data,
                "created_at": record.created_at.isoformat() if record.created_at else None,
            },
            "versions": [
                {
                    "artifact_id": version.id,
                    "version": version.version,
                    "title": version.title,
                    "created_at": (
                        version.created_at.isoformat() if version.created_at else None
                    ),
                }
                for version in versions
            ],
        }

    # ------------------------------------------------------------------
    # Tool 4: get_propagation_graph
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_propagation_graph(
        case_id: str,
        min_confidence: float | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Return a case's persisted posts (nodes) and propagation edges."""
        try:
            await repository.get_case(case_id)
        except ApplicationError:
            return {"ok": False, "found": False, "case_id": case_id}
        edges = await repository.list_propagation_edges_by_case(
            case_id,
            min_confidence=min_confidence,
            limit=max(1, min(limit, _MAX_LIMIT)),
        )
        posts = await social.list_posts_by_case(case_id) if social is not None else []
        post_ids = {post.id for post in posts}
        return {
            "ok": True,
            "found": True,
            "case_id": case_id,
            "nodes": [
                {
                    "post_id": post.id,
                    "native_id": post.native_id,
                    "platform": post.platform,
                    "content_type": post.content_type,
                    "title": post.title,
                    "content": post.content,
                    "author_name": post.author_name,
                    "source_url": post.source_url,
                    "published_at": (
                        post.published_at.isoformat() if post.published_at else None
                    ),
                    "engagement": post.engagement,
                }
                for post in posts
            ],
            "edges": [
                {
                    "edge_id": edge.id,
                    "source_post_id": edge.source_post_id,
                    "target_post_id": edge.target_post_id,
                    "relation": edge.relation,
                    "confidence": edge.confidence,
                    "feature_scores": edge.feature_scores,
                    "evidence_ids": edge.evidence_ids,
                    "algorithm_version": edge.algorithm_version,
                    "human_confirmed": edge.human_confirmed,
                }
                for edge in edges
                if edge.source_post_id in post_ids and edge.target_post_id in post_ids
            ],
            "node_count": len(posts),
            "edge_count": len(edges),
        }

    return mcp
