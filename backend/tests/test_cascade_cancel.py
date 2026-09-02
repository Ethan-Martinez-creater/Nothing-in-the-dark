"""Regression: cancelling an agent run must cascade-cancel its collection runs.

The coordinator run and the CollectionRun it triggered are driven by two
independent workers. Previously ``AgentRunService.cancel`` only stopped the
agent loop, leaving the background collection to keep running (the deep crawl
kept spinning after the user cancelled the coordinator run).
"""

from __future__ import annotations

from typing import Any

from app.application.agent_service import AgentRunService


class StubWorker:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel(self, run_id: str) -> None:
        self.cancelled.append(run_id)


class StubRepository:
    def __init__(self) -> None:
        self.updated: list[tuple[str, str]] = []

    async def update_agent_run(self, run_id: str, *, status: str) -> Any:
        self.updated.append((run_id, status))
        return {"id": run_id, "status": status}


class StubCollectionRuns:
    def __init__(self) -> None:
        self.cascaded: list[str] = []

    async def cancel_by_trigger_run(self, run_id: str) -> list[Any]:
        self.cascaded.append(run_id)
        return [{"id": "crun-1", "status": "cancelled"}]


async def test_cancel_cascades_to_collection_runs() -> None:
    worker = StubWorker()
    repository = StubRepository()
    collection_runs = StubCollectionRuns()
    service = AgentRunService(repository, worker, collection_runs)

    result = await service.cancel("run-abc")

    assert worker.cancelled == ["run-abc"]
    assert collection_runs.cascaded == ["run-abc"]
    assert repository.updated == [("run-abc", "cancelled")]
    assert result["status"] == "cancelled"


async def test_cancel_survives_cascade_failure() -> None:
    worker = StubWorker()
    repository = StubRepository()

    class BrokenCascade:
        async def cancel_by_trigger_run(self, run_id: str) -> list[Any]:  # noqa: ARG002
            raise RuntimeError("boom")

    service = AgentRunService(repository, worker, BrokenCascade())

    result = await service.cancel("run-abc")

    # 级联失败不能阻断 agent run 本身的取消。
    assert worker.cancelled == ["run-abc"]
    assert repository.updated == [("run-abc", "cancelled")]


async def test_cancel_without_collection_service_still_works() -> None:
    worker = StubWorker()
    repository = StubRepository()
    service = AgentRunService(repository, worker, None)

    result = await service.cancel("run-abc")

    assert worker.cancelled == ["run-abc"]
    assert repository.updated == [("run-abc", "cancelled")]
