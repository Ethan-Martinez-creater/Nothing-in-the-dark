from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class ApplicationError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "application_error",
        details: list[dict[str, str]] | dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details


class ResourceNotFoundError(ApplicationError):
    def __init__(self, resource: str, resource_id: str) -> None:
        super().__init__(
            f"{resource} '{resource_id}' does not exist",
            code="resource_not_found",
        )


class CrawlerConfigurationError(ApplicationError):
    """Raised when a real crawler cannot be started safely."""


class CrawlerExecutionError(ApplicationError):
    """Raised when the external crawler exits unsuccessfully."""


class A2ARemoteNotDeployedError(ApplicationError):
    """Raised when a remote A2A gateway is configured but not deployed.

    First delivery ships the local gateway only; the remote surface is a
    placeholder that answers 501 instead of pretending to work.
    """

    def __init__(self) -> None:
        super().__init__(
            "远程 A2A 服务未部署：当前仅支持本地 Agent 网关。",
            code="a2a_remote_not_deployed",
        )


class ApprovalRequiredError(ApplicationError):
    """Raised to pause a run until the user approves a risky or costly tool.

    The run must transition to ``waiting_approval`` and keep the pending
    tool call so it can resume from the exact interruption point.
    """

    def __init__(
        self,
        message: str,
        *,
        action: str,
        reason: str,
        request_payload: dict[str, object],
    ) -> None:
        super().__init__(message, code="approval_required")
        self.action = action
        self.reason = reason
        self.request_payload = request_payload


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ResourceNotFoundError)
    async def handle_not_found(
        _request: Request,
        exc: ResourceNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(ApplicationError)
    async def handle_application_error(
        _request: Request,
        exc: ApplicationError,
    ) -> JSONResponse:
        status_code = status.HTTP_400_BAD_REQUEST
        if isinstance(exc, A2ARemoteNotDeployedError):
            status_code = status.HTTP_501_NOT_IMPLEMENTED
        content: dict[str, object] = {"code": exc.code, "message": exc.message}
        if exc.details:
            content["details"] = exc.details
        return JSONResponse(status_code=status_code, content=content)
