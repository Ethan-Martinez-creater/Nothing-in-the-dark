"""M7: Report Document routes（产品层报告发布流）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import HTMLResponse

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.schemas.report_documents import (
    ImportReportRequest,
    ReportDocumentResponse,
    UpdateReportRequest,
)
from app.services.reports import render_html_report

router = APIRouter()
case_router = APIRouter()


@router.get("", response_model=list[ReportDocumentResponse])
async def list_reports(
    status_filter: str | None = None,
    container: ApplicationContainer = Depends(get_container),
) -> list[ReportDocumentResponse]:
    records = await container.report_document_service.list_global(status=status_filter)
    return [ReportDocumentResponse.from_record(record) for record in records]


@router.get("/{report_id}", response_model=ReportDocumentResponse)
async def get_report(
    report_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> ReportDocumentResponse:
    # 全局读取：scope 校验在 service（对象必须存在于请求 case 内的场景由
    # case-scoped 端点承担；全局视图直接返回）。
    record = await container.report_document_service.get_by_id(report_id)
    if record is None:
        from app.core.errors import ResourceNotFoundError

        error = ResourceNotFoundError("report", report_id)
        error.code = "report_not_found"
        raise error
    return ReportDocumentResponse.from_record(record)


@case_router.post(
    "/{case_id}/reports:from-artifact",
    response_model=ReportDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_report_from_artifact(
    case_id: str,
    request: ImportReportRequest,
    container: ApplicationContainer = Depends(get_container),
) -> ReportDocumentResponse:
    record = await container.report_document_service.import_from_artifact(
        case_id, request.artifact_id
    )
    return ReportDocumentResponse.from_record(record)


@router.patch("/{report_id}", response_model=ReportDocumentResponse)
async def update_report(
    report_id: str,
    request: UpdateReportRequest,
    container: ApplicationContainer = Depends(get_container),
) -> ReportDocumentResponse:
    record = await container.report_document_service.get_by_id(report_id)
    if record is None:
        from app.core.errors import ResourceNotFoundError

        error = ResourceNotFoundError("report", report_id)
        error.code = "report_not_found"
        raise error
    updated = await container.report_document_service.update_draft(
        record.case_id,
        report_id,
        expected_lock_version=request.expected_lock_version,
        title=request.title,
        content=request.content,
    )
    return ReportDocumentResponse.from_record(updated)


@case_router.post("/{case_id}/reports/{report_id}:submit-review")
async def submit_report_review(
    case_id: str,
    report_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> ReportDocumentResponse:
    record = await container.report_document_service.change_status(
        case_id, report_id, "in_review"
    )
    return ReportDocumentResponse.from_record(record)


@case_router.post("/{case_id}/reports/{report_id}:publish")
async def publish_report(
    case_id: str,
    report_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> ReportDocumentResponse:
    record = await container.report_document_service.change_status(
        case_id, report_id, "published"
    )
    return ReportDocumentResponse.from_record(record)


@case_router.post("/{case_id}/reports/{report_id}:archive")
async def archive_report(
    case_id: str,
    report_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> ReportDocumentResponse:
    record = await container.report_document_service.change_status(
        case_id, report_id, "archived"
    )
    return ReportDocumentResponse.from_record(record)


@case_router.post("/{case_id}/reports/{report_id}:revise")
async def revise_report(
    case_id: str,
    report_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> ReportDocumentResponse:
    record = await container.report_document_service.revise(case_id, report_id)
    return ReportDocumentResponse.from_record(record)


@router.get("/{report_id}/download", response_class=HTMLResponse)
async def download_report(
    report_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> Response:
    record = await container.report_document_service.get_by_id(report_id)
    if record is None:
        from app.core.errors import ResourceNotFoundError

        error = ResourceNotFoundError("report", report_id)
        error.code = "report_not_found"
        raise error
    html = render_html_report(
        record.content_json if isinstance(record.content_json, dict) else {}
    )
    return Response(
        content=html,
        media_type="text/html",
        headers={
            "Content-Disposition": f'attachment; filename="report-{report_id}.html"'
        },
    )
