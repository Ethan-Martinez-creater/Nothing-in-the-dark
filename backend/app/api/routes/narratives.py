"""10 叙事生命周期与纠错传播评估 API。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.infrastructure.database.models import (
    CorrectionEventRecord,
    CorrectionImpactAnalysisRecord,
    LifecycleSnapshotRecord,
    NarrativeClaimRecord,
    NarrativePostRecord,
    NarrativeRecord,
    NarrativeTransitionRecord,
    NarrativeVersionRecord,
)
from app.schemas.narratives import (
    CorrectionCreate,
    CorrectionResponse,
    MergeRequest,
    NarrativeResponse,
    NarrativeVersionResponse,
    SplitRequest,
)
from app.services import narrative as narrative_service

router = APIRouter()


def _engagement_total(value: object) -> int:
    """Normalize platform engagement dictionaries into a stable numeric total."""
    if isinstance(value, dict):
        return sum(
            int(metric) for metric in value.values()
            if isinstance(metric, (int, float)) and not isinstance(metric, bool)
        )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return 0


async def _require_case_narrative(
    container: ApplicationContainer, case_id: str, narrative_id: str
) -> NarrativeRecord:
    record = await container.repository.get_narrative(narrative_id)
    if record.case_id != case_id:
        raise HTTPException(status_code=404, detail="narrative not found")
    return record


def _post_to_dict(post: object) -> dict[str, object]:
    return {
        "id": getattr(post, "id", ""),
        "content": getattr(post, "content", ""),
        "published_at": getattr(post, "published_at", None),
        "platform": getattr(post, "platform", ""),
        "author_id": getattr(post, "author_id", ""),
        "engagement": _engagement_total(getattr(post, "engagement", 0)),
    }


@router.post("/{case_id}/narratives/analyze", status_code=202)
async def analyze_narratives(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """运行时间约束增量聚类，创建/更新叙事、版本、成员与生命周期快照。

    同步执行（帖子量可控）；相同 title 的叙事复用，成员追加（幂等）。
    """
    posts = await container.social.list_posts_by_case(case_id)
    clusterer = narrative_service.NarrativeClusterer()
    candidates = clusterer.cluster([_post_to_dict(p) for p in posts])

    created = 0
    updated = 0
    now = datetime.now(UTC)
    for candidate in candidates:
        existing = None
        for record in await container.repository.list_narratives(case_id):
            if record.title == candidate["title"]:
                existing = record
                break
        if existing is None:
            record = await container.repository.create_narrative(
                NarrativeRecord(
                    case_id=case_id,
                    title=candidate["title"],
                    canonical_summary="、".join(candidate["keywords"][:5]),
                    created_source="clusterer",
                )
            )
            created += 1
        else:
            record = existing
            updated += 1
        versions = await container.repository.list_narrative_versions(record.id, limit=1)
        latest = versions[0] if versions else None
        metrics = {"member_count": candidate["member_count"]}
        if (
            latest is None
            or latest.algorithm_version != candidate["algorithm_version"]
            or latest.keywords != candidate["keywords"]
            or latest.metrics != metrics
        ):
            await container.repository.add_narrative_version(
                NarrativeVersionRecord(
                    narrative_id=record.id,
                    data_watermark=now,
                    algorithm_version=candidate["algorithm_version"],
                    keywords=candidate["keywords"],
                    metrics=metrics,
                )
            )
        for member in candidate["members"]:
            post_id = str(member.get("id") or "")
            if not post_id:
                continue
            try:
                await container.repository.add_narrative_post(
                    NarrativePostRecord(
                        narrative_id=record.id,
                        post_id=post_id,
                        membership_score=0.8,
                        decision_source="auto",
                    )
                )
            except IntegrityError:
                pass  # 唯一键冲突 = 幂等
        # 生命周期快照（按平台聚合到小时桶）。
        await _persist_lifecycle(container, record.id, candidate["members"])
    return {"created": created, "updated": updated, "total": len(candidates)}


async def _persist_lifecycle(
    container: ApplicationContainer, narrative_id: str, members: list[dict[str, object]]
) -> None:
    from collections import defaultdict

    buckets: dict[tuple[datetime, str], dict[str, object]] = defaultdict(
        lambda: {"volume": 0, "accounts": set(), "engagement": 0}
    )
    for member in members:
        published = member.get("published_at")
        if isinstance(published, str):
            try:
                published = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except ValueError:
                continue
        if not isinstance(published, datetime):
            continue
        bucket = published.replace(minute=0, second=0, microsecond=0)
        platform = str(member.get("platform") or "")
        key = (bucket, platform)
        buckets[key]["volume"] = int(buckets[key]["volume"]) + 1
        buckets[key]["accounts"].add(str(member.get("author_id") or ""))
        buckets[key]["engagement"] = int(buckets[key]["engagement"]) + int(
            member.get("engagement") or 0
        )
    for (bucket, platform), stats in buckets.items():
        try:
            await container.repository.add_lifecycle_snapshot(
                LifecycleSnapshotRecord(
                    narrative_id=narrative_id,
                    time_bucket=bucket,
                    platform=platform,
                    volume=int(stats["volume"]),
                    unique_accounts=len(stats["accounts"]),
                    engagement=int(stats["engagement"]),
                    stage="unknown",
                )
            )
        except IntegrityError:
            pass


@router.get("/{case_id}/narratives", response_model=list[NarrativeResponse])
async def list_narratives(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[NarrativeResponse]:
    records = await container.repository.list_narratives(case_id)
    return [NarrativeResponse.model_validate(r) for r in records]


@router.get("/{case_id}/narratives/{narrative_id}")
async def get_narrative(
    case_id: str,
    narrative_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    record = await _require_case_narrative(container, case_id, narrative_id)
    versions = await container.repository.list_narrative_versions(narrative_id)
    members = await container.repository.list_narrative_members(narrative_id)
    snapshots = await container.repository.list_lifecycle_snapshots(narrative_id)
    timeline = [
        {
            "bucket": s.time_bucket.isoformat(),
            "platform": s.platform,
            "volume": s.volume,
            "unique_accounts": s.unique_accounts,
            "engagement": s.engagement,
            "stage": s.stage,
        }
        for s in snapshots
    ]
    return {
        "id": record.id,
        "case_id": record.case_id,
        "title": record.title,
        "canonical_summary": record.canonical_summary,
        "status": record.status,
        "review_state": record.review_state,
        "first_seen_label": narrative_service.first_seen_vs_origin_label(None),
        "versions": [
            NarrativeVersionResponse.model_validate(v).model_dump() for v in versions
        ],
        "members": members,
        "timeline": timeline,
    }


@router.get("/{case_id}/narratives/{narrative_id}/timeline")
async def narrative_timeline(
    case_id: str,
    narrative_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """生命周期阶段分析：数据缺口标注 unknown，不自动判定衰退。"""
    await _require_case_narrative(container, case_id, narrative_id)
    snapshots = await container.repository.list_lifecycle_snapshots(narrative_id)
    points = [
        {
            "bucket": s.time_bucket,
            "platform": s.platform,
            "volume": s.volume,
            "unique_accounts": s.unique_accounts,
            "engagement": s.engagement,
        }
        for s in snapshots
    ]
    analyzer = narrative_service.LifecycleAnalyzer()
    return analyzer.analyze(points)


@router.post("/{case_id}/narratives/{narrative_id}:merge")
async def merge_narratives(
    case_id: str,
    narrative_id: str,
    request: MergeRequest,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """人工合并：当前叙事成员迁移到目标叙事，当前叙事标记 archived。"""
    source = await _require_case_narrative(container, case_id, narrative_id)
    target = await _require_case_narrative(
        container, case_id, request.target_narrative_id
    )
    if source.id == target.id:
        raise HTTPException(status_code=422, detail="cannot merge a narrative into itself")
    members = await container.repository.list_narrative_members(narrative_id)
    for claim_id in members["claims"]:
        try:
            await container.repository.add_narrative_claim(
                NarrativeClaimRecord(
                    narrative_id=target.id,
                    claim_id=claim_id,
                    membership_score=1.0,
                    decision_source="human_merge",
                )
            )
        except IntegrityError:
            pass
    for post_id in members["posts"]:
        try:
            await container.repository.add_narrative_post(
                NarrativePostRecord(
                    narrative_id=target.id,
                    post_id=post_id,
                    membership_score=1.0,
                    decision_source="human_merge",
                )
            )
        except IntegrityError:
            pass
    source.status = "archived"
    source.review_state = "superseded"
    await container.repository.update_narrative_state(source)
    # M10 撤销：transition evidence 保存成员快照，供 undo-merge 精确回滚。
    await container.repository.add_narrative_transition(
        NarrativeTransitionRecord(
            narrative_id=source.id,
            from_variant=source.id,
            to_variant=target.id,
            transition_type="human_merge",
            first_seen=datetime.now(UTC),
            evidence={
                "case_id": case_id,
                "claims": list(members["claims"]),
                "posts": list(members["posts"]),
            },
        )
    )
    return {"merged_into": target.id, "archived": source.id}


@router.post("/{case_id}/narratives/{narrative_id}:undo-merge")
async def undo_merge_narrative(
    case_id: str,
    narrative_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """撤销人工合并：按快照从目标移除成员并恢复来源叙事（不静默覆盖）。"""
    source = await _require_case_narrative(container, case_id, narrative_id)
    if source.status != "archived":
        raise HTTPException(
            status_code=422, detail="narrative is not archived (cannot undo)"
        )
    transitions = await container.repository.list_narrative_transitions(
        narrative_id
    )
    merge_tx = next(
        (t for t in transitions if t.transition_type == "human_merge"), None
    )
    if merge_tx is None:
        raise HTTPException(
            status_code=422, detail="no human merge transition to undo"
        )
    evidence = merge_tx.evidence or {}
    removed = await container.repository.remove_narrative_members(
        target_narrative_id=merge_tx.to_variant,
        claim_ids=list(evidence.get("claims") or []),
        post_ids=list(evidence.get("posts") or []),
        decision_source="human_merge",
    )
    source.status = "active"
    source.review_state = "unreviewed"
    await container.repository.update_narrative_state(source)
    await container.repository.add_narrative_transition(
        NarrativeTransitionRecord(
            narrative_id=source.id,
            from_variant=merge_tx.to_variant,
            to_variant=source.id,
            transition_type="human_merge_undo",
            first_seen=datetime.now(UTC),
            evidence={"case_id": case_id, "removed_members": removed},
        )
    )
    return {
        "restored": source.id,
        "removed_members": removed,
        "from": merge_tx.to_variant,
    }


@router.post("/{case_id}/narratives/{narrative_id}:split")
async def split_narrative(
    case_id: str,
    narrative_id: str,
    request: SplitRequest,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """人工拆分：创建新的空叙事（后续可逐步 assign 成员）。"""
    source = await _require_case_narrative(container, case_id, narrative_id)
    created = await container.repository.create_narrative(
        NarrativeRecord(
            case_id=case_id,
            title=request.title or f"{source.title}（拆分）",
            canonical_summary="",
            created_source="human_split",
        )
    )
    await container.repository.add_narrative_transition(
        NarrativeTransitionRecord(
            narrative_id=source.id,
            from_variant=source.id,
            to_variant=created.id,
            transition_type="human_split",
            first_seen=datetime.now(UTC),
            evidence={"case_id": case_id},
        )
    )
    return {"narrative_id": created.id}

@router.post("/{case_id}/narratives/{narrative_id}:undo-split")
async def undo_split_narrative(
    case_id: str,
    narrative_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """撤销人工拆分：拆分出的叙事为空（无成员/证据）时归档恢复。

    若拆分出的叙事已有成员或证据则拒绝（防止静默数据丢失）；撤销记录
    追加 human_split_undo transition 供审计。
    """
    source = await _require_case_narrative(container, case_id, narrative_id)
    transitions = await container.repository.list_narrative_transitions(
        narrative_id
    )
    split_tx = next(
        (t for t in reversed(transitions) if t.transition_type == "human_split"),
        None,
    )
    if split_tx is None:
        raise HTTPException(
            status_code=422, detail="no human split transition to undo"
        )
    target = await container.repository.get_narrative(split_tx.to_variant)
    if target.status == "archived":
        raise HTTPException(
            status_code=422, detail="split narrative already undone",
        )
    members = await container.repository.list_narrative_members(
        split_tx.to_variant
    )
    if members["claims"] or members["posts"]:
        raise HTTPException(
            status_code=422,
            detail="split narrative has members; refusing to auto-undo",
        )
    target.status = "archived"
    target.review_state = "superseded"
    await container.repository.update_narrative_state(target)
    await container.repository.add_narrative_transition(
        NarrativeTransitionRecord(
            narrative_id=source.id,
            from_variant=split_tx.to_variant,
            to_variant=source.id,
            transition_type="human_split_undo",
            first_seen=datetime.now(UTC),
            evidence={"case_id": case_id, "archived": split_tx.to_variant},
        )
    )
    return {
        "restored": source.id,
        "archived_split": split_tx.to_variant,
    }


@router.get("/{case_id}/corrections", response_model=list[CorrectionResponse])
async def list_corrections(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[CorrectionResponse]:
    records = await container.repository.list_correction_events(case_id)
    return [CorrectionResponse.model_validate(r) for r in records]


@router.post(
    "/{case_id}/corrections", response_model=CorrectionResponse, status_code=201
)
async def add_correction(
    case_id: str,
    request: CorrectionCreate,
    container: ApplicationContainer = Depends(get_container),
) -> CorrectionResponse:
    if request.target_narrative_id:
        await _require_case_narrative(
            container, case_id, request.target_narrative_id
        )
    record = CorrectionEventRecord(
        case_id=case_id,
        source_post_id=request.source_post_id,
        claim_id=request.claim_id,
        target_narrative_id=request.target_narrative_id,
        correction_type=request.correction_type,
        content=request.content,
        publisher_class=request.publisher_class,
    )
    saved = await container.repository.add_correction_event(record)
    return CorrectionResponse.model_validate(saved)


@router.post("/{case_id}/corrections/{correction_id}/impact")
async def correction_impact(
    case_id: str,
    correction_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """纠错影响分析：描述性前后对比，默认不声称因果。"""
    events = await container.repository.list_correction_events(case_id, limit=500)
    event = next((e for e in events if e.id == correction_id), None)
    if event is None:
        raise HTTPException(status_code=404, detail="correction event not found")
    posts = await container.social.list_posts_by_case(case_id)
    correction_time = event.created_at or datetime.now(UTC)
    window_start = correction_time - timedelta(hours=24)
    window_end = correction_time + timedelta(hours=24)
    before = [
        _post_to_dict(p)
        for p in posts
        if _time(p) and window_start <= _time(p) < correction_time
    ]
    after = [
        _post_to_dict(p)
        for p in posts
        if _time(p) and correction_time <= _time(p) <= window_end
    ]
    analyzer = narrative_service.CorrectionAnalyzer()
    result = analyzer.analyze(
        correction_time=correction_time, before=before, after=after
    )
    saved = await container.repository.add_correction_impact(
        CorrectionImpactAnalysisRecord(
            case_id=case_id,
            correction_event_id=event.id,
            narrative_id=event.target_narrative_id,
            window=dict(result["window"]),
            method=str(result["method"]),
            metrics=dict(result["metrics"]),
            limitations=list(result["limitations"]),
            result=str(result["result"]),
            confidence_level=str(result["confidence_level"]),
            causal_claim=bool(result["causal_claim"]),
        )
    )
    return {**result, "analysis_id": saved.id}


def _time(post: object) -> datetime | None:
    value = getattr(post, "published_at", None)
    if isinstance(value, datetime):
        return value
    return None
