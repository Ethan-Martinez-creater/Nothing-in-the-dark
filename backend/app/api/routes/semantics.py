"""11 中文复杂语义与跨语言分析 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.infrastructure.database.models import (
    AnnotationCorrectionRecord,
    LexiconEntryRecord,
    SemanticAnnotationRecord,
)
from app.schemas.semantics import (
    AnalyzeRequest,
    AnalyzeResponse,
    CorrectionCreate,
    CorrectionResponse,
    LexiconEntryCreate,
    LexiconEntryResponse,
)
from app.services import semantics

router = APIRouter()


@router.get("/{case_id}/semantics/lexicon", response_model=list[LexiconEntryResponse])
async def list_lexicon(
    case_id: str,
    domain: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    container: ApplicationContainer = Depends(get_container),
) -> list[LexiconEntryResponse]:
    records = await container.repository.list_lexicon_entries(
        domain=domain, platform=platform, limit=200
    )
    return [LexiconEntryResponse.model_validate(r) for r in records]


@router.post(
    "/{case_id}/semantics/lexicon",
    response_model=LexiconEntryResponse,
    status_code=201,
)
async def add_lexicon_entry(
    case_id: str,
    request: LexiconEntryCreate,
    container: ApplicationContainer = Depends(get_container),
) -> LexiconEntryResponse:
    record = LexiconEntryRecord(
        term=request.term,
        normalized=request.normalized or request.term,
        meaning=request.meaning,
        domain=request.domain,
        platform=request.platform,
        language=request.language,
        valid_from=request.valid_from,
        valid_to=request.valid_to,
        source=request.source or "api",
        review_state=request.review_state,
    )
    saved = await container.repository.add_lexicon_entry(record)
    return LexiconEntryResponse.model_validate(saved)


@router.post("/{case_id}/semantics/analyze", response_model=AnalyzeResponse)
async def analyze_semantics(
    case_id: str,
    request: AnalyzeRequest,
    container: ApplicationContainer = Depends(get_container),
) -> AnalyzeResponse:
    """对文本做统一语义分析（规则基线 + 可选 LLM），并落库标注。

    LLM 不可用或输出未通过质量门时自动回退规则结果（fallback=true）。
    """
    entries = await container.repository.list_lexicon_entries(
        domain=request.domain or None,
        platform=request.platform or None,
        review_state="approved",
        limit=500,
    )
    lexicon = [
        semantics.LexiconEntry(
            term=e.term,
            normalized=e.normalized,
            meaning=e.meaning,
            domain=e.domain,
            platform=e.platform,
            language=e.language,
            valid_from=e.valid_from,
            valid_to=e.valid_to,
            review_state=e.review_state,
        )
        for e in entries
    ]
    payload = await semantics.analyze_text(
        request.text,
        request.tasks,
        lexicon=lexicon,
        platform=request.platform,
        domain=request.domain,
        llm=container.llm,
    )
    # 落库标注（人工纠错入口依赖 annotation_id）。
    if request.source_id:
        for result in payload["results"]:
            span = result.get("span")
            annotation = SemanticAnnotationRecord(
                case_id=case_id,
                source_type=request.source_type,
                source_id=request.source_id,
                task=str(result["task"]),
                label=str(result["label"]),
                span_start=span[0] if span else None,
                span_end=span[1] if span else None,
                confidence=float(result.get("confidence") or 0),
                provider=str(result.get("provider") or "rules"),
                model_version=payload["semantic_version"],
                lexicon_version="lexicon-1.0.0",
            )
            try:
                await container.repository.add_semantic_annotation(annotation)
            except IntegrityError:
                # 唯一键冲突视为幂等，不阻断分析返回。
                pass
    return AnalyzeResponse.model_validate(payload)


@router.post(
    "/{case_id}/semantics/corrections",
    response_model=CorrectionResponse,
    status_code=201,
)
async def add_semantic_correction(
    case_id: str,
    request: CorrectionCreate,
    container: ApplicationContainer = Depends(get_container),
) -> CorrectionResponse:
    """人工语义纠错：原结果保留，修正写入追加记录（不覆盖历史）。"""
    annotation = await container.repository.get_semantic_annotation(
        request.annotation_id
    )
    if annotation.case_id != case_id:
        raise HTTPException(status_code=404, detail="semantic annotation not found")
    record = AnnotationCorrectionRecord(
        annotation_id=request.annotation_id,
        original=request.original,
        corrected=request.corrected,
        reason=request.reason,
        actor=request.actor,
    )
    saved = await container.repository.add_annotation_correction(record)
    return CorrectionResponse.model_validate(saved)


@router.get(
    "/{case_id}/semantics/annotations",
    response_model=list[dict[str, object]],
)
async def list_semantic_annotations(
    case_id: str,
    source_id: str | None = Query(default=None),
    task: str | None = Query(default=None),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict[str, object]]:
    records = await container.repository.list_semantic_annotations(
        case_id=case_id, source_id=source_id, task=task, limit=200
    )
    return [
        {
            "id": r.id,
            "case_id": r.case_id,
            "source_type": r.source_type,
            "source_id": r.source_id,
            "task": r.task,
            "label": r.label,
            "span": [r.span_start, r.span_end] if r.span_start is not None else None,
            "entity_ref": r.entity_ref,
            "confidence": r.confidence,
            "provider": r.provider,
            "model_version": r.model_version,
            "lexicon_version": r.lexicon_version,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]


