from __future__ import annotations

import asyncio
from pathlib import Path

from app.application.repositories import ApplicationRepository
from app.application.runner import AnalysisRunner
from app.graphs.case_analysis import CaseAnalysisGraph
from app.harness.sandbox import SandboxedToolExecutor
from app.harness.tool_factory import build_tool_registry
from app.infrastructure.crawler import DemoCrawlerAdapter
from app.infrastructure.database import Database
from app.schemas.cases import CreateCaseRequest
from app.schemas.tasks import StartAnalysisRequest


async def test_analysis_runner_persists_events_and_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    tools = build_tool_registry(DemoCrawlerAdapter())
    tools.set_sandbox_executor(
        SandboxedToolExecutor(base_env={"COIFESP_DEMO_MODE": "1"})
    )
    graph = CaseAnalysisGraph(repository, tools)
    runner = AnalysisRunner(repository, graph)

    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="跨平台测试事件", platforms=["weibo", "bilibili"])
        )
        task = await runner.start(case.id, StartAnalysisRequest())
        active = runner._active.get(task.id)
        assert active is not None
        await asyncio.wait_for(asyncio.shield(active), timeout=60)

        current = await repository.get_task(task.id)
        artifacts = await repository.list_artifacts(case.id)
        events = await repository.list_events(task.id)

        assert current.status == "completed", current.error
        assert {artifact.kind for artifact in artifacts} == {
            "dataset",
            "opinion_analysis",
            "propagation_graph",
            "fact_check",
            "report",
        }
        assert events[-1].stage == "completed"
    finally:
        await runner.stop()
        await database.dispose()
