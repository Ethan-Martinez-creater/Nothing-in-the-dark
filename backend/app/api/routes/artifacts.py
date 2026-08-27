from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import HTMLResponse

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ApplicationError
from app.schemas.runs import AgentRunResponse
from app.schemas.tasks import ArtifactResponse
from app.services.reports import diff_reports, render_html_report

router = APIRouter()


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> ArtifactResponse:
    record = await container.repository.get_artifact(artifact_id)
    return ArtifactResponse.model_validate(record)


@router.get("/{artifact_id}/versions", response_model=list[ArtifactResponse])
async def list_artifact_versions(
    artifact_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[ArtifactResponse]:
    records = await container.repository.list_artifact_versions(artifact_id)
    return [ArtifactResponse.model_validate(record) for record in records]


@router.get("/{artifact_id}/download", response_class=HTMLResponse)
async def download_artifact_html(
    artifact_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> HTMLResponse:
    """导出报告 HTML（导出前自动对敏感信息打码）。"""
    record = await container.repository.get_artifact(artifact_id)
    if record.kind != "report":
        raise ApplicationError(
            "HTML 导出仅支持 report Artifact",
            code="unsupported_artifact_kind",
        )
    rendered = render_html_report(dict(record.data))
    return HTMLResponse(
        content=rendered,
        headers={
            "Content-Disposition": (
                f'attachment; filename="report-{artifact_id[:8]}.html"'
            )
        },
    )


@router.get("/{artifact_id}/diff", response_model=dict[str, object])
async def diff_artifact(
    artifact_id: str,
    against: str = Query(..., description="对比对象的 Artifact ID"),
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    """报告版本差异比较（章节级结构化 diff）。"""
    current = await container.repository.get_artifact(artifact_id)
    previous = await container.repository.get_artifact(against)
    if current.kind != "report" or previous.kind != "report":
        raise ApplicationError(
            "版本差异比较仅支持 report Artifact",
            code="unsupported_artifact_kind",
        )
    return diff_reports(dict(current.data), dict(previous.data))


@router.post(
    "/{artifact_id}/regenerate",
    response_model=AgentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_artifact(
    artifact_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> AgentRunResponse:
    """基于全部已有 Artifact 重新生成报告（走持久化 Run 链路，
    新产出自动成为同 (case_id, kind) 族的下一版本）。"""
    artifact = await container.repository.get_artifact(artifact_id)
    if artifact.kind != "report":
        raise ApplicationError(
            "重生成仅支持 report Artifact",
            code="unsupported_artifact_kind",
        )
    case = await container.repository.get_case(artifact.case_id)
    content = (
        f"请基于案例「{case.title}」的全部已有 Artifact（观点分析、传播重建、"
        f"事实核查、证据审查及历史报告）重新生成一份完整报告，结论必须逐条"
        f"绑定存在且属于当前案例的 Evidence ID。"
    )
    record = await container.agent_service.start(
        case_id=artifact.case_id,
        content=content,
        approve_crawl=False,
    )
    return AgentRunResponse.model_validate(record)
