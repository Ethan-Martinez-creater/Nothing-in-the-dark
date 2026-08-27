from __future__ import annotations

import asyncio
from typing import Any

from app.application.repositories import ApplicationRepository
from app.domain.enums import ArtifactKind, CaseStatus, EventType, TaskStatus
from app.graphs.case_analysis import CaseAnalysisGraph
from app.harness.state import AnalysisState
from app.schemas.tasks import StartAnalysisRequest


class AnalysisRunner:
    """LEGACY in-process runner for ``CaseAnalysisGraph``.

    Production must not call ``start`` / ``recover``. Tests that still
    need the old Task timeline construct this class themselves.
    """

    def __init__(
        self,
        repository: ApplicationRepository,
        graph: CaseAnalysisGraph,
        *,
        demo_mode: bool = True,
    ) -> None:
        self._repository = repository
        self._graph = graph
        self._demo_mode = demo_mode
        self._active: dict[str, asyncio.Task[None]] = {}

    async def start(
        self,
        case_id: str,
        request: StartAnalysisRequest,
    ) -> Any:
        task = await self._repository.create_task(case_id, request)
        await self._repository.add_event(
            task.id,
            event_type=EventType.STATUS,
            stage="queued",
            message="分析任务已进入队列",
            progress=0,
            payload={"demo_mode": self._demo_mode},
        )
        self._schedule(task.id)
        return task

    async def recover(self) -> None:
        for task in await self._repository.list_recoverable_tasks():
            self._schedule(task.id)

    def _schedule(self, task_id: str) -> None:
        active = self._active.get(task_id)
        if active is not None and not active.done():
            return
        created = asyncio.create_task(self._execute(task_id), name=f"analysis:{task_id}")
        self._active[task_id] = created
        created.add_done_callback(lambda _: self._active.pop(task_id, None))

    async def _execute(self, task_id: str) -> None:
        task = await self._repository.get_task(task_id)
        case = await self._repository.get_case(task.case_id)
        try:
            await self._repository.set_case_status(case.id, CaseStatus.RUNNING)
            await self._repository.update_task(
                task_id,
                status=TaskStatus.RUNNING,
                current_stage="starting",
                progress=0.02,
            )
            state: AnalysisState = {
                "task_id": task.id,
                "case_id": case.id,
                "topic": case.topic,
                "platforms": list(case.platforms),
                "time_range": dict(case.time_range),
                "options": dict(task.options),
            }
            result = await self._graph.run(state)
            await self._persist_artifacts(result)
            await self._repository.add_event(
                task_id,
                event_type=EventType.STATUS,
                stage="completed",
                message="首版 Harness 分析流程已完成",
                progress=1,
                payload={"artifact_count": 5, "demo_mode": result["is_demo"]},
            )
            await self._repository.update_task(
                task_id,
                status=TaskStatus.COMPLETED,
                current_stage="completed",
                progress=1,
            )
            await self._repository.set_case_status(case.id, CaseStatus.COMPLETED)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._repository.update_task(
                task_id,
                status=TaskStatus.FAILED,
                current_stage="failed",
                error=str(exc),
            )
            await self._repository.set_case_status(case.id, CaseStatus.FAILED)
            await self._repository.add_event(
                task_id,
                event_type=EventType.ERROR,
                stage="failed",
                message="分析任务执行失败",
                progress=0,
                payload={"error": str(exc)},
            )

    async def _persist_artifacts(self, state: AnalysisState) -> None:
        artifacts = [
            (
                ArtifactKind.DATASET,
                "归一化社交平台样本",
                {"posts": state["posts"], "is_demo": state["is_demo"]},
            ),
            (ArtifactKind.OPINION_ANALYSIS, "舆论分析", state["opinion"]),
            (ArtifactKind.PROPAGATION_GRAPH, "跨平台传播图", state["propagation"]),
            (ArtifactKind.FACT_CHECK, "事实核查卡片", state["fact_check"]),
            (ArtifactKind.REPORT, "结构化舆情简报", state["report"]),
        ]
        for kind, title, data in artifacts:
            artifact = await self._repository.create_artifact(
                case_id=state["case_id"],
                task_id=state["task_id"],
                kind=kind,
                title=title,
                data=data,
            )
            await self._repository.add_event(
                state["task_id"],
                event_type=EventType.ARTIFACT,
                stage="artifact",
                message=f"已保存：{title}",
                progress=0.98,
                payload={"artifact_id": artifact.id, "kind": kind},
            )

    async def stop(self) -> None:
        tasks = list(self._active.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
