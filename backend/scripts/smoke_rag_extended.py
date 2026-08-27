"""PostgreSQL smoke acceptance for extended RAG sources.

Requires a real PostgreSQL database (connection from settings, overridable
with the DATABASE_URL env var) and an optional embedding worker. Verifies
that a single hybrid query returns comments, artifacts, claims and evidence
with stable evidence ids and platform/time filters.

Run from Project\\backend (mirrors smoke_phase1_claims_evidence.py):
    .venv\\Scripts\\python scripts\\smoke_rag_extended.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.application.repositories import ApplicationRepository  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.infrastructure.database import Database  # noqa: E402
from app.infrastructure.database.knowledge_repository import (  # noqa: E402
    KnowledgeRepository,
)
from app.infrastructure.database.models import (  # noqa: E402
    ArtifactRecord,
    EvidenceRecord,
    SourceCommentRecord,
    SourcePostRecord,
)
from app.schemas.cases import CreateCaseRequest  # noqa: E402


async def _main() -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    await database.create_schema()
    repository = ApplicationRepository(database)
    knowledge = KnowledgeRepository(database)

    case = await repository.create_case(
        CreateCaseRequest(topic="冒烟：RAG 扩展源", platforms=["weibo"])
    )
    # claims.created_by_run_id is a real FK to agent_runs on PostgreSQL, so
    # the claim must be created through the repository with a real run id.
    run = await repository.create_agent_run(
        case_id=case.id,
        turn_id=None,
        objective="冒烟种子",
    )
    # Keyword search requires ALL query terms (ILIKE ALL), so every seeded
    # source must contain both "召回" and "股价" to be retrieved by
    # query="召回 股价".
    async with database.session_factory() as session:
        post = SourcePostRecord(
            case_id=case.id,
            platform="weibo",
            native_id="smoke-post-1",
            content="官方确认该批次车辆启动主动召回，股价短期承压。",
            source_url="https://weibo.com/smoke",
            published_at=datetime(2026, 8, 1, tzinfo=UTC),
            content_hash="smoke-hash-1",
        )
        session.add(post)
        await session.flush()
        session.add(
            SourceCommentRecord(
                post_id=post.id,
                platform="weibo",
                native_id="smoke-comment-1",
                content="召回消息公布后股价下跌。",
                published_at=datetime(2026, 8, 2, tzinfo=UTC),
            )
        )
        session.add(
            ArtifactRecord(
                case_id=case.id,
                kind="fact_check",
                title="召回范围核查卡",
                version=1,
                data={"verdict": "supported", "关注": "股价波动"},
            )
        )
        session.add(
            EvidenceRecord(
                case_id=case.id,
                source_type="social_post",
                source_id="smoke-post-1",
                stance="support",
                excerpt="官方公告确认主动召回，股价应声下跌。",
            )
        )
        await session.commit()
    await repository.create_claim(
        case_id=case.id,
        text="该批次召回引发股价下跌担忧",
        created_by_run_id=run.id,
    )

    hits = await knowledge.search(case_id=case.id, query="召回 股价", limit=20)
    types = {hit.source_type for hit in hits}
    assert {"social_comment", "artifact", "claim", "evidence"} <= types, (
        f"missing sources: {types}"
    )
    assert all(hit.evidence_id.startswith(f"{hit.source_type}:") for hit in hits)
    assert all(hit.retrieval_modes for hit in hits)

    filtered = await knowledge.search(
        case_id=case.id,
        query="召回",
        limit=20,
        source_types={"social_post", "social_comment"},
        platforms=["bilibili"],
    )
    assert filtered == [], f"platform filter leaked: {filtered}"

    print(
        "SMOKE OK:",
        {
            source_type: sum(1 for h in hits if h.source_type == source_type)
            for source_type in types
        },
    )
    await database.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
