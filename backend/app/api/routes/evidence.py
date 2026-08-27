"""Evidence summary endpoints (案例证据汇总侧栏)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.evidence import (
    ClaimEvidenceResponse,
    EvidenceItemResponse,
    EvidenceSummaryResponse,
    ReviewClaimRequest,
)

router = APIRouter()

_EXCERPT_LIMIT = 160


def _post_to_evidence_item(post: object) -> EvidenceItemResponse:
    """把采集帖子映射成未归属证据行：即使尚未跑事实核查，证据侧栏也能
    展示案例已入库的社交内容（excerpt 截断，metadata 携带平台上下文）。"""
    metadata: dict[str, object] = {
        "platform": getattr(post, "platform", ""),
        "author": getattr(post, "author_name", ""),
        "source_url": getattr(post, "source_url", ""),
        "content_type": getattr(post, "content_type", "post"),
    }
    published_at = getattr(post, "published_at", None)
    if published_at is not None:
        metadata["published_at"] = published_at.isoformat()
    content = str(getattr(post, "content", "") or "")
    title = str(getattr(post, "title", "") or "")
    excerpt = "\n".join(part for part in (title, content) if part).strip()
    if len(excerpt) > _EXCERPT_LIMIT:
        excerpt = f"{excerpt[:_EXCERPT_LIMIT]}…"
    return EvidenceItemResponse(
        id=f"post:{post.id}",
        case_id=post.case_id,
        claim_id=None,
        source_type="social_post",
        source_id=post.id,
        stance="context",
        excerpt=excerpt,
        relevance=0.5,
        metadata_json=metadata,
        created_at=post.created_at,
    )


@router.get(
    "/{case_id}/evidence-summary",
    response_model=EvidenceSummaryResponse,
)
async def get_evidence_summary(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> EvidenceSummaryResponse:
    """Return all claims of a case with their evidence grouped per claim,
    plus evidence rows not attached to any claim (``unassigned``).

    Evidence within each claim keeps the repository ordering (relevance
    descending), so the sidebar can render the strongest support first.
    Collected social posts are appended to ``unassigned`` so the sidebar
    reflects case evidence even before fact-checking produced claims.
    """
    repository = container.repository
    await repository.get_case(case_id)  # 404 for unknown case
    claims = await repository.list_claims_by_case(case_id)
    records = await repository.list_evidence_by_case(case_id)

    by_claim: dict[str, list[EvidenceItemResponse]] = {}
    unassigned: list[EvidenceItemResponse] = []
    for record in records:
        item = EvidenceItemResponse.model_validate(record)
        if record.claim_id is not None:
            by_claim.setdefault(record.claim_id, []).append(item)
        else:
            unassigned.append(item)

    # 已被核查证据行引用的帖子不再重复进 unassigned。核查写入的
    # source_id 是采集帖子的对外 id（platform-native_id，如 weibo-abc），
    # 入库主键是 UUID，两种形态都参与去重。
    claimed_source_ids = {
        row.source_id for rows in by_claim.values() for row in rows
    } | {row.source_id for row in unassigned}
    posts = await container.social.list_posts_by_case(case_id)
    for post in posts:
        post_keys = {
            post.id,
            post.native_id,
            f"{post.platform}:{post.native_id}",
        }
        if post_keys & claimed_source_ids:
            continue
        unassigned.append(_post_to_evidence_item(post))

    claim_responses = [
        ClaimEvidenceResponse.model_validate(claim).model_copy(
            update={"evidence": by_claim.get(claim.id, [])}
        )
        for claim in claims
    ]
    return EvidenceSummaryResponse(
        case_id=case_id,
        claims=claim_responses,
        unassigned=unassigned,
    )


@router.post(
    "/{case_id}/claims/{claim_id}/review",
    response_model=ClaimEvidenceResponse,
)
async def review_claim(
    case_id: str,
    claim_id: str,
    request: ReviewClaimRequest,
    container: ApplicationContainer = Depends(get_container),
) -> ClaimEvidenceResponse:
    record = await container.repository.review_claim(
        case_id,
        claim_id,
        confirmed=request.confirmed,
        note=request.note,
    )
    return ClaimEvidenceResponse.model_validate(record)
