from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.bootstrap import ApplicationContainer
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.telemetry import TraceContext, reset_trace, root_context, set_trace


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        container = ApplicationContainer(resolved_settings)
        await container.start()
        application.state.container = container
        yield
        await container.stop()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        description="Social-platform opinion analysis Harness Agent",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix=resolved_settings.api_prefix)
    register_exception_handlers(application)

    @application.middleware("http")
    async def telemetry_middleware(request: Request, call_next):
        """M19: http.request span + request_id 注入 + API 指标。

        telemetry 不可用/未装配时原样放行，绝不阻塞业务。
        """
        container = getattr(request.app.state, "container", None)
        telemetry = getattr(container, "telemetry", None) if container else None
        if telemetry is None:
            return await call_next(request)
        request_id = uuid4().hex
        ctx = root_context(
            attributes={"request_id": request_id, "worker_id": "http"}
        )
        token = set_trace(ctx)
        span = telemetry.tracer.start_span(
            "http.request",
            kind="server",
            attributes={
                "http.method": request.method,
                "http.route": request.url.path,
                "request_id": request_id,
            },
            parent=ctx,
        )
        token = set_trace(
            TraceContext(
                trace_id=span.trace_id,
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                attributes=dict(ctx.attributes),
            )
        )
        started = time.perf_counter()
        try:
            response = await call_next(request)
            span.attributes["http.status_code"] = response.status_code
            telemetry.metrics.increment("api.requests")
            if response.status_code >= 500:
                telemetry.metrics.increment("api.errors")
            return response
        except Exception as exc:  # noqa: BLE001 - 记录后继续抛出
            span.attributes["http.status_code"] = 500
            telemetry.metrics.increment("api.errors")
            from app.telemetry.redact import redact_exception_chain

            span.events.append(
                {"name": "exception", "message": redact_exception_chain(exc)}
            )
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            telemetry.metrics.observe("api.latency_ms", elapsed_ms)
            telemetry.tracer.end_span(span)
            reset_trace(token)

    return application


app = create_app()


if __name__ == "__main__":
    # psycopg async (used by the LangGraph PostgreSQL checkpointer) cannot
    # run on the ProactorEventLoop. uvicorn >= 0.51 hardcodes
    # ProactorEventLoop on Windows via its loop factory, so a plain
    # `uvicorn app.main:app` fails at startup; run the Server ourselves on
    # a SelectorEventLoop instead (start with `.venv\Scripts\python -m app.main`).
    from uvicorn import Config, Server

    async def _serve() -> None:
        server = Server(Config("app.main:app", host="127.0.0.1", port=8000))
        await server.serve()

    # asyncio.run(loop_factory=...) is only available in newer Python
    # releases. Runner supports the explicit loop factory on Python 3.11,
    # which is the canonical BettaFish environment for this workspace.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_serve())
