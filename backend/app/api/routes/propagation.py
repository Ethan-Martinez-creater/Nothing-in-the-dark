"""Propagation edge human-confirmation endpoints (M2) + graph DTO (C7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.propagation import (
    ConfirmPropagationEdgeRequest,
    PropagationEdgeResponse,
    PropagationGraphNode,
    PropagationGraphResponse,
)

router = APIRouter()


@router.get(
    "/{case_id}/propagation-edges",
    response_model=list[PropagationEdgeResponse],
)
async def list_propagation_edges(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[PropagationEdgeResponse]:
    """Return persisted edges with human-confirmation state so the frontend
    can restore confirmed/rejected badges after a reload (BUG-3)."""
    records = await container.repository.list_propagation_edges_by_case(case_id)
    return [PropagationEdgeResponse.model_validate(r) for r in records]


@router.get(
    "/{case_id}/propagation-graph",
    response_model=PropagationGraphResponse,
)
async def get_propagation_graph(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> PropagationGraphResponse:
    """C7: 传播图 DTO —— nodes 按 post 去重聚合（roles 列表 + 最高分主
    role），edges 复用既有字段；label/excerpt 来自 SourcePostRecord。
    """
    nodes, edges, posts = await container.repository.list_propagation_graph(case_id)
    grouped: dict[str, list] = {}
    for node in nodes:
        grouped.setdefault(node.post_id, []).append(node)

    graph_nodes: list[PropagationGraphNode] = []
    for post_id, group in grouped.items():
        primary = group[0]  # list_propagation_graph 已按 score desc 排序
        post = posts.get(post_id)
        title = (post.title or "").strip() if post else ""
        content = (post.content or "").strip() if post else ""
        label = title or (post.author_name if post else "") or post_id
        excerpt = f"{title} {content}".strip()[:160]
        graph_nodes.append(
            PropagationGraphNode(
                post_id=post_id,
                role=primary.role,
                roles=[item.role for item in group],
                score=float(primary.score),
                attributes=dict(primary.attributes or {}),
                algorithm_version=primary.algorithm_version,
                platform=post.platform if post else "unknown",
                label=label,
                excerpt=excerpt,
                published_at=(
                    post.published_at.isoformat() if post and post.published_at else None
                ),
                author_name=(post.author_name if post else "") or "",
            )
        )
    return PropagationGraphResponse(
        nodes=graph_nodes,
        edges=[PropagationEdgeResponse.model_validate(edge) for edge in edges],
    )


@router.post(
    "/{case_id}/propagation-edges/{edge_id}/confirmation",
    response_model=PropagationEdgeResponse,
)
async def confirm_propagation_edge(
    case_id: str,
    edge_id: str,
    request: ConfirmPropagationEdgeRequest,
    container: ApplicationContainer = Depends(get_container),
) -> PropagationEdgeResponse:
    record = await container.repository.confirm_propagation_edge(
        case_id,
        edge_id,
        confirmed=request.confirmed,
        note=request.note,
    )
    return PropagationEdgeResponse.model_validate(record)
