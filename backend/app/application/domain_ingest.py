"""Persist accounts, entities, propagation nodes, artifact refs and costs.

Called after crawl / reconstruction / run completion so domain tables stay
in sync with posts and artifacts (P1-2.2). Failures must not block the
caller — each function is best-effort and returns a small counter dict.
"""

from __future__ import annotations

import logging
from typing import Any

from app.application.repositories import ApplicationRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.services.media_features import persist_media_from_posts
from app.services.propagation_algorithm import (
    extract_entities,
    normalize_account_name,
)

logger = logging.getLogger(__name__)


def artifact_references(data: dict[str, Any]) -> dict[str, list[str]]:
    """Collect stable IDs already present on an artifact payload."""
    refs: dict[str, list[str]] = {
        "evidence_ids": [],
        "claim_ids": [],
        "edge_ids": [],
        "post_ids": [],
        "artifact_ids": [],
    }

    def _add(bucket: str, value: object) -> None:
        text = str(value or "").strip()
        if text and text not in refs[bucket]:
            refs[bucket].append(text)

    for key in ("evidence_ids", "citation_links", "supporting_evidence"):
        raw = data.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    for eid in item.get("evidence_ids") or []:
                        _add("evidence_ids", eid)
                    _add("claim_ids", item.get("id") or item.get("claim_id"))
                else:
                    _add("evidence_ids", item)
    for card in data.get("cards") or []:
        if not isinstance(card, dict):
            continue
        _add("claim_ids", card.get("id"))
        for eid in (card.get("supporting_evidence") or []) + (
            card.get("contradicting_evidence") or []
        ):
            _add("evidence_ids", eid)
        _add("post_ids", card.get("source_post_id"))
    for edge in data.get("edges") or []:
        if isinstance(edge, dict):
            _add("edge_ids", edge.get("edge_id") or edge.get("id"))
            _add("post_ids", edge.get("source"))
            _add("post_ids", edge.get("target"))
    for node in data.get("nodes") or []:
        if isinstance(node, dict):
            _add("post_ids", node.get("id"))
    for item in data.get("explanation", {}).get("evidence_ids") or []:
        _add("post_ids", item)
    return refs


async def ingest_accounts_from_posts(
    repository: ApplicationRepository,
    case_id: str,
    posts: list[dict[str, Any]],
) -> dict[str, int]:
    created = 0
    for post in posts:
        name = str(post.get("author") or "").strip()
        if not name:
            continue
        native_id = str(
            post.get("author_id") or post.get("author_native_id") or name
        )
        try:
            await repository.upsert_account(
                case_id=case_id,
                platform=str(post.get("platform") or ""),
                native_id=native_id,
                name=name,
                normalized_name=normalize_account_name(name),
                follower_count=int(post.get("follower_count") or 0),
                verified=bool(post.get("verified")),
            )
            created += 1
        except Exception:
            logger.exception("account upsert failed for %s", name)
    return {"accounts": created}


async def ingest_entities_from_posts(
    repository: ApplicationRepository,
    case_id: str,
    posts: list[dict[str, Any]],
) -> dict[str, int]:
    count = 0
    for post in posts:
        content = str(post.get("content") or "")
        for token in extract_entities(content):
            try:
                await repository.upsert_entity(
                    case_id=case_id,
                    entity_type="extracted",
                    name=token,
                    normalized_name=token.lower(),
                )
                count += 1
            except Exception:
                logger.exception("entity upsert failed for %s", token)
    return {"entities": count}


async def ingest_after_crawl(
    repository: ApplicationRepository,
    social: SocialRepository,
    case_id: str,
    posts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Accounts + entities + media after posts are persisted."""
    stats: dict[str, Any] = {}
    stats.update(await ingest_accounts_from_posts(repository, case_id, posts))
    stats.update(await ingest_entities_from_posts(repository, case_id, posts))
    try:
        stats["media"] = await persist_media_from_posts(
            repository, social, case_id, posts
        )
    except Exception:
        logger.exception("media persist failed")
        stats["media"] = {"created": 0, "skipped": 1}
    return stats


async def ingest_propagation_nodes(
    repository: ApplicationRepository,
    social: SocialRepository,
    case_id: str,
    graph: dict[str, Any],
) -> int:
    posts = await social.list_posts_by_case(case_id)
    native_to_db = {str(post.native_id): post.id for post in posts}
    written = 0
    for role_row in graph.get("node_roles") or []:
        if not isinstance(role_row, dict):
            continue
        raw_id = str(role_row.get("post_id") or "")
        post_id = native_to_db.get(raw_id, raw_id)
        if not any(post.id == post_id for post in posts):
            continue
        try:
            await repository.create_propagation_node(
                case_id=case_id,
                post_id=post_id,
                role=str(role_row.get("role") or "spreader"),
                score=float(role_row.get("score") or 0),
                attributes={
                    "out_degree": role_row.get("out_degree"),
                    "in_degree": role_row.get("in_degree"),
                },
                algorithm_version=str(graph.get("algorithm_version") or "1.1.0"),
            )
            written += 1
        except Exception:
            logger.exception("propagation node persist failed for %s", raw_id)
    return written


async def persist_run_cost_summary(
    repository: ApplicationRepository,
    run_id: str,
    case_id: str,
) -> None:
    trace = await repository.get_run_trace(run_id)
    model_cost = sum(float(item.estimated_cost or 0) for item in trace["model_calls"])
    tool_cost = sum(float(item.estimated_cost or 0) for item in trace["tool_calls"])
    await repository.upsert_cost_summary(
        summary_type="run",
        run_id=run_id,
        case_id=case_id,
        model_cost=model_cost,
        tool_cost=tool_cost,
        total_cost=model_cost + tool_cost,
    )
