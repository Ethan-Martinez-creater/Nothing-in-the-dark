"""Real-PostgreSQL durability acceptance check.

Simulates the acceptance criterion from remaining.md 3.3: an Agent run stops
mid-way (approval interrupt), the "process" is discarded, a brand-new worker
with a fresh PostgreSQL checkpointer connection resumes from the checkpoint,
and the run completes without duplicating posts, tool calls or artifacts.

The first phase ends inside the approval interrupt (simulating a crash or a
deliberate stop). The second phase resumes with the user's approval decision.
Everything runs against the real configured PostgreSQL database; no LLM is
involved (scripted FakeGateway).

Run from Project\\backend:
    .venv\\Scripts\\python scripts\\verify_durable_recovery.py
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from pydantic import BaseModel

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.application.agent_service import AgentRunService  # noqa: E402
from app.application.graph_worker import GraphWorker  # noqa: E402
from app.application.ports.crawler import CrawlRequest, SocialCrawlerPort  # noqa: E402
from app.application.repositories import ApplicationRepository  # noqa: E402
from app.bootstrap import create_checkpointer  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.harness.skills import SkillRegistry  # noqa: E402
from app.harness.tool_factory import build_tool_registry  # noqa: E402
from app.infrastructure.crawler.demo import DemoCrawlerAdapter  # noqa: E402
from app.infrastructure.database import Database  # noqa: E402
from app.infrastructure.database.knowledge_repository import KnowledgeRepository  # noqa: E402
from app.infrastructure.database.social_repository import SocialRepository  # noqa: E402
from app.infrastructure.embeddings import EmbeddingWorkerClient  # noqa: E402
from app.infrastructure.llm import (  # noqa: E402
    LLMGateway,
    LLMMessage,
    LLMResponse,
    ModelRoute,
    ToolCall,
)
from app.schemas.cases import CreateCaseRequest  # noqa: E402


class CrawlInput(BaseModel):
    topic: str
    platforms: list[str]
    time_range: dict[str, str | None]


class FakeGateway(LLMGateway):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def configured(self) -> bool:
        return True

    async def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        route: ModelRoute,
        temperature: float = 0,
    ) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                message=LLMMessage(role="assistant"),
                tool_calls=[
                    ToolCall(
                        id="durable-crawl-call",
                        name="collect_social_posts",
                        arguments={
                            "topic": "耐久性验证",
                            "platforms": ["weibo"],
                            "time_range": {"start": None, "end": None},
                        },
                    )
                ],
                model="fake-model",
            )
        return LLMResponse(
            message=LLMMessage(role="assistant", content="恢复后完成分析"),
            model="fake-model",
        )


class CountingCrawler(SocialCrawlerPort):
    def __init__(self) -> None:
        self.calls = 0
        self._inner = DemoCrawlerAdapter()

    async def collect(self, request: CrawlRequest) -> list[dict[str, Any]]:
        self.calls += 1
        return await self._inner.collect(request)


async def phase_a(
    database_url: str,
    *,
    case_id: str,
    gateway: FakeGateway,
    crawler: CountingCrawler,
) -> tuple[str, str]:
    """First "process": enqueue the run, execute until the approval interrupt,
    then stop as if the process was killed."""
    database = Database(database_url)
    await database.create_schema()
    repository = ApplicationRepository(database)
    checkpointer, checkpointer_cm = await create_checkpointer(database_url)
    tools = build_tool_registry(
        crawler,
        SkillRegistry(),
        KnowledgeRepository(database),
        EmbeddingWorkerClient("", dimensions=1024, timeout_seconds=120),
        SocialRepository(database),
    )
    worker = GraphWorker(
        repository,
        gateway,
        tools,
        SkillRegistry(),
        worker_id="phase-a",
        poll_interval_seconds=0.05,
        lease_seconds=300,
        max_turns=8,
        max_tool_calls=16,
        max_cost=5.0,
        checkpointer=checkpointer,
    )
    service = AgentRunService(repository, worker)
    run = await service.start(case_id=case_id, content="采集并分析", approve_crawl=False)
    await worker.tick(wait=True)
    run = await repository.get_agent_run(run.id)
    print(f"[phase A] run {run.id} status={run.status}")
    assert run.status == "waiting_approval", run.status
    assert crawler.calls == 0, "crawler must not run before approval"
    approvals = await repository.list_pending_approvals(run.id)
    assert len(approvals) == 1
    await checkpointer_cm.__aexit__(None, None, None)
    await database.dispose()
    return run.id, approvals[0].id


async def phase_b(
    database_url: str,
    *,
    run_id: str,
    approval_id: str,
    gateway: FakeGateway,
    crawler: CountingCrawler,
) -> None:
    """Second "process": a fresh connection resumes the run from the
    PostgreSQL checkpoint with the approval decision."""
    database = Database(database_url)
    await database.create_schema()
    repository = ApplicationRepository(database)
    checkpointer, checkpointer_cm = await create_checkpointer(database_url)
    tools = build_tool_registry(
        crawler,
        SkillRegistry(),
        KnowledgeRepository(database),
        EmbeddingWorkerClient("", dimensions=1024, timeout_seconds=120),
        SocialRepository(database),
    )
    worker = GraphWorker(
        repository,
        gateway,
        tools,
        SkillRegistry(),
        worker_id="phase-b",
        poll_interval_seconds=0.05,
        lease_seconds=300,
        max_turns=8,
        max_tool_calls=16,
        max_cost=5.0,
        checkpointer=checkpointer,
    )
    service = AgentRunService(repository, worker)
    run = await service.approve(
        run_id, approval_id=approval_id, decision=True, note="verification"
    )
    await worker.tick(wait=True)
    run = await repository.get_agent_run(run_id)
    print(f"[phase B] run {run_id} status={run.status}")
    assert run.status == "completed", run.error
    assert crawler.calls == 1, "crawler must run exactly once across restart"
    trace = await repository.get_run_trace(run_id)
    assert len(trace["tool_calls"]) == 1, "tool call must not be duplicated"
    assert trace["tool_calls"][0].status == "completed"
    assert len(trace["model_calls"]) == 2, "model calls resume, not replay"
    assert trace["approvals"][0].status == "approved"
    turns = await repository.list_turns(run.case_id)
    assert any(turn.role == "assistant" for turn in turns)
    await checkpointer_cm.__aexit__(None, None, None)
    await database.dispose()
    print("[phase B] OK: resumed from PostgreSQL checkpoint without duplication")


async def main() -> None:
    settings = get_settings()
    database_url = settings.database_url
    if not database_url.startswith("postgresql"):
        raise SystemExit(
            "This acceptance check requires the real PostgreSQL database "
            "(DATABASE_URL=postgresql+asyncpg://...) in backend/.env"
        )

    database = Database(database_url)
    await database.create_schema()
    repository = ApplicationRepository(database)
    case = await repository.create_case(
        CreateCaseRequest(
            topic="耐久性恢复验收",
            platforms=["weibo"],
            description="verify_durable_recovery.py acceptance case",
        )
    )
    case_id = case.id
    await database.dispose()
    print(f"case {case_id} created")

    gateway = FakeGateway()
    crawler = CountingCrawler()
    run_id, approval_id = await phase_a(
        database_url, case_id=case_id, gateway=gateway, crawler=crawler
    )
    await phase_b(
        database_url,
        run_id=run_id,
        approval_id=approval_id,
        gateway=gateway,
        crawler=crawler,
    )
    print("DURABILITY ACCEPTANCE PASSED")


if __name__ == "__main__":
    asyncio.run(main())
