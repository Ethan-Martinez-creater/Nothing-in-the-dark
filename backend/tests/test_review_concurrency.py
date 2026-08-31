"""RH6: Review lifecycle optimistic concurrency（C1–C11）专项测试。

验证 Post-V2 Review Decision Concurrency Hardening：

- ReviewItem.current_version 是单调递增的 lifecycle revision：claim/release/
  decision/reopen/Finding re-review 激活各 +1；幂等 submit / 只读操作不递增。
- Review decision 通过数据库条件 UPDATE（id + expected_status +
  expected_version）获得唯一状态转换权：CAS 失败者 0 ReviewDecision、
  0 Finding 变化。
- ReviewDecision.object_version 记录决策开始时的旧版本。
- ABA：旧 expected_version 即使 status 回到 in_review 也会 conflict。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.application.finding_service import FindingService
from app.application.repositories import ApplicationRepository
from app.application.review_service import ReviewService
from app.core.errors import ApplicationError
from app.infrastructure.database import Database
from app.infrastructure.database.models import (
    Base,
    FindingRecord,
    ReviewDecisionRecord,
    ReviewItemRecord,
)
from app.schemas.cases import CreateCaseRequest

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")


class _ScopedDatabase:
    """PG 集成测试用鸭子类型 Database：把连接固定在专用 schema 上。"""

    def __init__(self, url: str, schema: str) -> None:
        self._schema = schema
        # search_path 先指向专用 schema（建表目标），保留 public 以解析
        # pgvector 的 vector 类型（扩展默认安装在 public）。
        self.engine = create_async_engine(
            url,
            connect_args={"server_settings": {"search_path": f"{schema},public"}},
            pool_pre_ping=True,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()


async def _seed(
    database: Database,
) -> tuple[ApplicationRepository, FindingService, ReviewService, str]:
    await database.create_schema()
    repository = ApplicationRepository(database)
    finding_service = FindingService(database, repository)
    review_service = ReviewService(repository)
    case = await repository.create_case(
        CreateCaseRequest(topic="Concurrency 案例", platforms=["weibo"])
    )
    return repository, finding_service, review_service, case.id


async def _count_decisions(database: Database, item_id: str) -> int:
    async with database.session_factory() as session:
        return int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(ReviewDecisionRecord)
                    .where(ReviewDecisionRecord.item_id == item_id)
                )
            )
            or 0
        )


async def _get_item(database: Database, item_id: str) -> ReviewItemRecord:
    async with database.session_factory() as session:
        record = await session.get(ReviewItemRecord, item_id)
        assert record is not None
        return record


async def _get_finding(database: Database, finding_id: str) -> FindingRecord:
    async with database.session_factory() as session:
        record = await session.get(FindingRecord, finding_id)
        assert record is not None
        return record


async def _new_generic_item(
    database: Database, review_service: ReviewService, case_id: str
) -> ReviewItemRecord:
    """创建非 finding ReviewItem（claim 类型），走 generic 提交路径。"""
    return await review_service.submit_item(
        case_id=case_id, object_type="claim", object_id="claim-001", summary="并发主张"
    )


async def _new_finding_item(
    database: Database,
    repository: ApplicationRepository,
    finding_service: FindingService,
    case_id: str,
) -> tuple[FindingRecord, ReviewItemRecord]:
    finding = await finding_service.create_manual(case_id, statement="并发审核结论")
    return await repository.submit_finding_for_review(
        case_id=case_id, finding_id=finding.id
    )


# --------------------------------------------------------------------------
# C1: Queue version —— 新 item 在 queue DTO 中暴露 current_version=1（RH4）。
# --------------------------------------------------------------------------


async def test_c1_queue_version_new_item_is_1(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'c1.db'}")
    _repository, _finding_service, review_service, case_id = await _seed(database)
    item = await _new_generic_item(database, review_service, case_id)

    queue = await review_service.list_queue(case_id=case_id)
    row = next(r for r in queue if r["id"] == item.id)
    assert row["current_version"] == 1


# --------------------------------------------------------------------------
# C2: Claim —— unreviewed v1 → in_review v2。
# --------------------------------------------------------------------------


async def test_c2_claim_increments_version(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'c2.db'}")
    repository, _finding_service, review_service, case_id = await _seed(database)
    item = await _new_generic_item(database, review_service, case_id)
    assert item.current_version == 1

    claimed = await review_service.claim(item.id, "tester", case_id=case_id)
    assert claimed.status == "in_review"
    assert claimed.current_version == 2


# --------------------------------------------------------------------------
# C3: Release —— in_review v2 → unreviewed v3。
# --------------------------------------------------------------------------


async def test_c3_release_increments_version(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'c3.db'}")
    repository, _finding_service, review_service, case_id = await _seed(database)
    item = await _new_generic_item(database, review_service, case_id)
    await review_service.claim(item.id, "tester", case_id=case_id)

    released = await review_service.release(item.id, "tester", case_id=case_id)
    assert released.status == "unreviewed"
    assert released.current_version == 3


# --------------------------------------------------------------------------
# C4: Decision —— in_review v2 → accepted v3，ReviewDecision.object_version=2。
# --------------------------------------------------------------------------


async def test_c4_decision_increments_version_and_records_old_version(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'c4.db'}")
    repository, _finding_service, review_service, case_id = await _seed(database)
    item = await _new_generic_item(database, review_service, case_id)
    await review_service.claim(item.id, "tester", case_id=case_id)

    decided = await review_service.decide(
        item_id=item.id,
        decision="approved",
        reason="核实无误",
        actor="tester",
        case_id=case_id,
    )
    assert decided.status == "accepted"
    assert decided.current_version == 3

    decisions = await repository.list_review_decisions(item.id)
    assert decisions and decisions[0].object_version == 2


# --------------------------------------------------------------------------
# C5: Reopen —— accepted v3 → in_review v4，Finding verified → under_review。
# --------------------------------------------------------------------------


async def test_c5_reopen_increments_version_and_syncs_finding(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'c5.db'}")
    repository, finding_service, review_service, case_id = await _seed(database)
    finding, item = await _new_finding_item(
        database, repository, finding_service, case_id
    )
    await review_service.claim(item.id, "tester", case_id=case_id)
    await review_service.decide(
        item_id=item.id,
        decision="approved",
        reason="通过",
        actor="tester",
        case_id=case_id,
    )
    assert (await _get_finding(database, finding.id)).status == "verified"

    reopened = await review_service.reopen(item.id, actor="tester", case_id=case_id)
    assert reopened.status == "in_review"
    assert reopened.current_version == 4
    assert (await _get_finding(database, finding.id)).status == "under_review"


# --------------------------------------------------------------------------
# C6: Finding re-review —— accepted vN → in_review vN+1（复用同一 item）。
# --------------------------------------------------------------------------


async def test_c6_finding_rereview_increments_version_same_item(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'c6.db'}")
    repository, finding_service, review_service, case_id = await _seed(database)
    finding, item = await _new_finding_item(
        database, repository, finding_service, case_id
    )
    await review_service.claim(item.id, "tester", case_id=case_id)
    decided = await review_service.decide(
        item_id=item.id,
        decision="approved",
        reason="首审通过",
        actor="tester",
        case_id=case_id,
    )
    assert decided.current_version == 3
    assert (await _get_finding(database, finding.id)).status == "verified"

    # verified Finding 复审：同一 ReviewItem 重新激活到 in_review，版本 +1。
    _r_finding, reactivated = await repository.submit_finding_for_review(
        case_id=case_id, finding_id=finding.id
    )
    assert reactivated.id == item.id
    assert reactivated.status == "in_review"
    assert reactivated.current_version == 4
    assert (await _get_finding(database, finding.id)).status == "under_review"


# --------------------------------------------------------------------------
# C7: 幂等 submit —— ReviewItem.status 不变 → version 不变。
# --------------------------------------------------------------------------


async def test_c7_idempotent_submit_keeps_version(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'c7.db'}")
    repository, finding_service, review_service, case_id = await _seed(database)
    finding, item = await _new_finding_item(
        database, repository, finding_service, case_id
    )
    assert item.current_version == 1

    # 重复 submit（Finding 已 under_review、item 已 unreviewed）→ 幂等。
    _r_finding, again = await repository.submit_finding_for_review(
        case_id=case_id, finding_id=finding.id
    )
    assert again.id == item.id
    assert again.status == "unreviewed"
    assert again.current_version == 1


# --------------------------------------------------------------------------
# C8: Stale explicit version —— current v4，request expected v3 → conflict，
#     0 ReviewDecision、0 Finding change。
# --------------------------------------------------------------------------


async def test_c8_stale_explicit_version_conflicts(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'c8.db'}")
    repository, finding_service, review_service, case_id = await _seed(database)
    finding, item = await _new_finding_item(
        database, repository, finding_service, case_id
    )
    await review_service.claim(item.id, "tester", case_id=case_id)
    await review_service.decide(
        item_id=item.id,
        decision="approved",
        reason="通过",
        actor="tester",
        case_id=case_id,
    )
    await review_service.reopen(item.id, actor="tester", case_id=case_id)
    assert (await _get_item(database, item.id)).current_version == 4
    before = await _count_decisions(database, item.id)

    # service 层：显式旧版本 → review_version_conflict。
    with pytest.raises(ApplicationError) as excinfo:
        await review_service.decide(
            item_id=item.id,
            decision="rejected",
            reason="旧页面",
            actor="tester",
            case_id=case_id,
            expected_version=3,
        )
    assert excinfo.value.code == "review_version_conflict"
    assert await _count_decisions(database, item.id) == before
    assert (await _get_finding(database, finding.id)).status == "under_review"

    # repository 层：CAS 直接以旧版本竞争同样失败（0 decision / 0 Finding 变化）。
    stale = await repository.decide_review_item(
        item_id=item.id,
        expected_status="in_review",
        expected_version=3,
        target_status="rejected",
        decision=ReviewDecisionRecord(
            item_id=item.id,
            object_version=3,
            decision="rejected",
            reason="直接 CAS 旧版本",
            actor="tester",
        ),
    )
    assert stale is None
    assert await _count_decisions(database, item.id) == before
    assert (await _get_finding(database, finding.id)).status == "under_review"


# --------------------------------------------------------------------------
# C9: ABA regression —— snapshot in_review v2 → approve v3 → reopen v4 →
#     old expected v2 → conflict（即使 status 回到 in_review）。
# --------------------------------------------------------------------------


async def test_c9_aba_old_expected_version_conflicts(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'c9.db'}")
    repository, finding_service, review_service, case_id = await _seed(database)
    finding, item = await _new_finding_item(
        database, repository, finding_service, case_id
    )
    await review_service.claim(item.id, "tester", case_id=case_id)
    snapshot_version = (await _get_item(database, item.id)).current_version
    assert snapshot_version == 2

    await review_service.decide(
        item_id=item.id,
        decision="approved",
        reason="首轮通过",
        actor="tester",
        case_id=case_id,
    )
    await review_service.reopen(item.id, actor="tester", case_id=case_id)
    # status 已回到 in_review，但版本已到 4。
    assert (await _get_item(database, item.id)).status == "in_review"
    assert (await _get_item(database, item.id)).current_version == 4
    before = await _count_decisions(database, item.id)

    with pytest.raises(ApplicationError) as excinfo:
        await review_service.decide(
            item_id=item.id,
            decision="rejected",
            reason="旧轮次操作",
            actor="tester",
            case_id=case_id,
            expected_version=snapshot_version,
        )
    assert excinfo.value.code == "review_version_conflict"
    assert await _count_decisions(database, item.id) == before
    assert (await _get_finding(database, finding.id)).status == "under_review"


# --------------------------------------------------------------------------
# C10: Sequential CAS duplicate —— 同一 expected_status/version，第一成功、
#      第二返回 None，ReviewDecision 只增加 1。
# --------------------------------------------------------------------------


async def test_c10_sequential_cas_duplicate_single_winner(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'c10.db'}")
    repository, finding_service, review_service, case_id = await _seed(database)
    finding, item = await _new_finding_item(
        database, repository, finding_service, case_id
    )
    await review_service.claim(item.id, "tester", case_id=case_id)
    assert (await _get_item(database, item.id)).current_version == 2

    first = await repository.decide_review_item(
        item_id=item.id,
        expected_status="in_review",
        expected_version=2,
        target_status="accepted",
        decision=ReviewDecisionRecord(
            item_id=item.id,
            object_version=2,
            decision="approved",
            reason="第一次",
            actor="tester",
        ),
    )
    assert first is not None
    assert first[0].status == "accepted"
    assert first[0].current_version == 3

    # 同 expected_status/version 的第二次必须失败（CAS rowcount=0）。
    second = await repository.decide_review_item(
        item_id=item.id,
        expected_status="in_review",
        expected_version=2,
        target_status="rejected",
        decision=ReviewDecisionRecord(
            item_id=item.id,
            object_version=2,
            decision="rejected",
            reason="第二次",
            actor="tester",
        ),
    )
    assert second is None

    assert await _count_decisions(database, item.id) == 1
    assert (await _get_item(database, item.id)).current_version == 3
    assert (await _get_finding(database, finding.id)).status == "verified"


# --------------------------------------------------------------------------
# C11: Concurrent opposite decisions —— 两个独立事务同时以 approved/rejected
#      提交同一 expected version：exactly one winner / one loser，ReviewDecision
#      只 +1，Finding 状态匹配 winner。不依赖"approved 一定赢"。
# --------------------------------------------------------------------------


async def test_c11_concurrent_opposite_decisions_single_winner(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'c11.db'}")
    repository, finding_service, review_service, case_id = await _seed(database)
    finding, item = await _new_finding_item(
        database, repository, finding_service, case_id
    )
    await review_service.claim(item.id, "tester", case_id=case_id)

    async def decide_with(target_status: str, decision: str, reason: str):
        return await repository.decide_review_item(
            item_id=item.id,
            expected_status="in_review",
            expected_version=2,
            target_status=target_status,
            decision=ReviewDecisionRecord(
                item_id=item.id,
                object_version=2,
                decision=decision,
                reason=reason,
                actor="tester",
            ),
        )

    results = await asyncio.gather(
        decide_with("accepted", "approved", "并发通过"),
        decide_with("rejected", "rejected", "并发拒绝"),
    )
    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1, f"expected exactly one winner, got {results!r}"
    assert len(losers) == 1

    winner_item, winner_decision = winners[0]
    assert winner_item.current_version == 3
    expected_status = "accepted" if winner_decision.decision == "approved" else "rejected"
    assert winner_item.status == expected_status
    assert await _count_decisions(database, item.id) == 1

    if winner_decision.decision == "approved":
        assert (await _get_finding(database, finding.id)).status == "verified"
    else:
        assert winner_decision.decision == "rejected"
        assert (await _get_finding(database, finding.id)).status == "rejected"


@pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL unavailable; PostgreSQL concurrent race not executed",
)
async def test_c11_postgres_concurrent_opposite_decisions() -> None:
    """PostgreSQL 真并发（two sessions + barrier + opposite decisions）。

    两个独立连接在各自读到 v2 快照后同时 CAS，exactly one winner。
    使用专用 schema（review_concurrency_test），结束清理，不触碰生产数据。
    """
    schema = "review_concurrency_test"
    # 幂等创建专用 schema，并确保 pgvector 扩展可用（库级、受信任扩展）。
    import asyncpg

    base_url = TEST_POSTGRES_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
    admin = await asyncio.wait_for(asyncpg.connect(base_url, timeout=6), timeout=10)
    try:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await admin.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    finally:
        await admin.close()

    database = _ScopedDatabase(TEST_POSTGRES_URL, schema)
    try:
        await database.create_schema()
        repository = ApplicationRepository(database)
        finding_service = FindingService(database, repository)
        review_service = ReviewService(repository)
        case = await repository.create_case(
            CreateCaseRequest(topic="PG 并发案例", platforms=["weibo"])
        )
        finding = await finding_service.create_manual(case.id, statement="PG 并发结论")
        _f, item = await repository.submit_finding_for_review(
            case_id=case.id, finding_id=finding.id
        )
        await review_service.claim(item.id, "tester", case_id=case.id)

        ev_a_ready = asyncio.Event()
        ev_b_ready = asyncio.Event()
        ev_a_go = asyncio.Event()
        ev_b_go = asyncio.Event()

        async def race(
            ready: asyncio.Event, go: asyncio.Event, target_status: str, decision: str
        ):
            # 各自读 v2 快照，然后等 barrier 后同时 CAS。
            repo = ApplicationRepository(database)
            snapshot = await repo.get_review_item(item.id)
            assert snapshot.current_version == 2
            ready.set()
            await asyncio.wait_for(go.wait(), timeout=30)
            return await repo.decide_review_item(
                item_id=item.id,
                expected_status=snapshot.status,
                expected_version=snapshot.current_version,
                target_status=target_status,
                decision=ReviewDecisionRecord(
                    item_id=item.id,
                    object_version=snapshot.current_version,
                    decision=decision,
                    reason="PG 并发",
                    actor="tester",
                ),
            )

        task_a = asyncio.create_task(
            race(ev_a_ready, ev_a_go, "accepted", "approved")
        )
        task_b = asyncio.create_task(
            race(ev_b_ready, ev_b_go, "rejected", "rejected")
        )
        await asyncio.wait_for(ev_a_ready.wait(), timeout=30)
        await asyncio.wait_for(ev_b_ready.wait(), timeout=30)
        ev_a_go.set()
        ev_b_go.set()
        results = await asyncio.gather(task_a, task_b)

        winners = [r for r in results if r is not None]
        losers = [r for r in results if r is None]
        assert len(winners) == 1
        assert len(losers) == 1
        winner_item, winner_decision = winners[0]
        assert winner_item.current_version == 3
        assert await _count_decisions(database, item.id) == 1
        final = await _get_finding(database, finding.id)
        if winner_decision.decision == "approved":
            assert final.status == "verified"
        else:
            assert final.status == "rejected"
    finally:
        await database.dispose()
        admin = await asyncio.wait_for(asyncpg.connect(base_url, timeout=6), timeout=10)
        try:
            await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await admin.close()
