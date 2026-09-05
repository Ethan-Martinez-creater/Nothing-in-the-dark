from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import get_container
from app.bootstrap import ApplicationContainer
from app.core.errors import ApplicationError
from app.schemas.cases import (
    CaseResponse,
    CreateCaseRequest,
    CreateTurnRequest,
    TurnResponse,
    UpdateCaseRequest,
)
from app.schemas.domain import (
    AccountResponse,
    CostSummaryResponse,
    EvaluationResponse,
)
from app.schemas.runs import AgentRunResponse, CreateMessageRequest
from app.schemas.tasks import ArtifactResponse, StartAnalysisRequest, TaskResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("", response_model=CaseResponse, status_code=status.HTTP_201_CREATED)
async def create_case(
    request: CreateCaseRequest,
    container: ApplicationContainer = Depends(get_container),
) -> CaseResponse:
    record = await container.repository.create_case(request)
    # 创建会话不自动生成主题消息：对话从用户输入第一条指令（POST /messages）
    # 才真正开始，空会话由前端引导卡承接。
    return CaseResponse.model_validate(record)


@router.patch("/{case_id}", response_model=CaseResponse)
async def update_case(
    case_id: str,
    request: UpdateCaseRequest,
    container: ApplicationContainer = Depends(get_container),
) -> CaseResponse:
    record = await container.repository.update_case(
        case_id,
        **request.model_dump(exclude_unset=True),
    )
    return CaseResponse.model_validate(record)


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> None:
    await container.repository.delete_case(case_id)
    # FC2：删除成功后 best-effort enqueue 一次 global advanced signal
    # refresh（workspace 仍有剩余 Case 才会创建）。enqueue 失败不影响
    # 已提交的删除。
    try:
        await container.intelligence_refresh_service.enqueue_after_scope_deletion(
            scope_key=case_id
        )
    except Exception:  # best-effort follow-up（FC2 §28）
        logger.warning(
            "advanced_signal_refresh enqueue after case delete %s failed",
            case_id,
            exc_info=True,
        )


@router.get("", response_model=list[CaseResponse])
async def list_cases(
    container: ApplicationContainer = Depends(get_container),
) -> list[CaseResponse]:
    records = await container.repository.list_cases()
    return [CaseResponse.model_validate(record) for record in records]


@router.get("/{case_id}", response_model=CaseResponse)
async def get_case(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> CaseResponse:
    record = await container.repository.get_case(case_id)
    return CaseResponse.model_validate(record)


@router.get("/{case_id}/turns", response_model=list[TurnResponse])
async def list_turns(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[TurnResponse]:
    records = await container.repository.list_turns(case_id)
    return [TurnResponse.model_validate(record) for record in records]


@router.post(
    "/{case_id}/turns",
    response_model=TurnResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_turn(
    case_id: str,
    request: CreateTurnRequest,
    container: ApplicationContainer = Depends(get_container),
) -> TurnResponse:
    record = await container.repository.add_turn(
        case_id,
        role="user",
        content=request.content,
    )
    return TurnResponse.model_validate(record)


@router.post(
    "/{case_id}/analysis",
    response_model=AgentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_analysis(
    case_id: str,
    request: StartAnalysisRequest,
    response: Response,
    container: ApplicationContainer = Depends(get_container),
) -> AgentRunResponse:
    """Deprecated compatibility shim.

    Converts the old analysis request into an Agent message so every
    production result still flows through the durable Agent Run pipeline
    (Run Event, Model Call, Tool Call and Artifact). New callers must use
    ``POST /cases/{id}/messages``.
    """
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "true"
    response.headers["Link"] = (
        f'</api/v1/cases/{case_id}/messages>; rel="successor-version"'
    )
    case = await container.repository.get_case(case_id)
    if not container.settings.demo_mode and not request.force_crawl:
        raise ApplicationError(
            "Real platform crawling requires explicit approval",
            code="crawl_approval_required",
        )
    fact_check = "包含事实核查" if request.include_fact_check else "跳过事实核查"
    crawl = (
        "已批准采集社交平台数据。"
        if request.force_crawl or container.settings.demo_mode
        else "不采集新数据，仅基于已有数据进行分析。"
    )
    content = (
        f"请对案例「{case.title}」（主题：{case.topic}，平台：{case.platforms}，"
        f"时间范围：{case.time_range}）执行完整舆情分析。{fact_check}。"
        f"最高预算 ¥{request.max_budget}。{crawl}"
    )
    record = await container.agent_service.start(
        case_id=case_id,
        content=content,
        approve_crawl=request.force_crawl,
    )
    return AgentRunResponse.model_validate(record)


@router.post(
    "/{case_id}/messages",
    response_model=AgentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_agent_message(
    case_id: str,
    request: CreateMessageRequest,
    container: ApplicationContainer = Depends(get_container),
) -> AgentRunResponse:
    record = await container.agent_service.start(
        case_id=case_id,
        content=request.content,
        approve_crawl=request.approve_crawl,
        artifact_id=request.artifact_id,
        ui_context=(
            request.ui_context.model_dump(exclude_none=True)
            if request.ui_context is not None
            else None
        ),
    )
    return AgentRunResponse.model_validate(record)


@router.get("/{case_id}/tasks", response_model=list[TaskResponse])
async def list_case_tasks(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[TaskResponse]:
    records = await container.repository.list_case_tasks(case_id)
    return [TaskResponse.model_validate(record) for record in records]


@router.get("/{case_id}/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[ArtifactResponse]:
    records = await container.repository.list_artifacts(case_id)
    return [ArtifactResponse.model_validate(record) for record in records]


@router.get("/{case_id}/accounts", response_model=list[AccountResponse])
async def list_case_accounts(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[AccountResponse]:
    await container.repository.get_case(case_id)
    records = await container.repository.list_accounts(case_id=case_id)
    return [AccountResponse.model_validate(record) for record in records]


@router.get("/{case_id}/evaluations", response_model=list[EvaluationResponse])
async def list_case_evaluations(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[EvaluationResponse]:
    await container.repository.get_case(case_id)
    records = await container.repository.list_evaluations(case_id=case_id)
    return [EvaluationResponse.model_validate(record) for record in records]


@router.get("/{case_id}/cost-summaries", response_model=list[CostSummaryResponse])
async def list_case_cost_summaries(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[CostSummaryResponse]:
    await container.repository.get_case(case_id)
    records = await container.repository.list_cost_summaries(case_id=case_id)
    return [CostSummaryResponse.model_validate(record) for record in records]


@router.get("/{case_id}/runs", response_model=list[AgentRunResponse])
async def list_case_runs(
    case_id: str,
    container: ApplicationContainer = Depends(get_container),
) -> list[AgentRunResponse]:
    records = await container.repository.list_agent_runs(case_id)
    return [AgentRunResponse.model_validate(record) for record in records]
