"""FC5: deterministic E2E fixture producer for the Final Closure browser run.

Writes the fixture set through the normal models/repositories (no raw SQL)
into the database configured via DATABASE_URL (the E2E backend's own SQLite
file) and prints a single JSON line with the real IDs on the last stdout
line, so ``frontend/e2e-interact.cjs`` can drive the UI with stable targets.

Usage (after the E2E backend has started once so the schema exists, or it
will be created here):

    set DATABASE_URL=sqlite+aiosqlite:///./data/e2e_closure.db
    python scripts/seed_final_closure_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.finding_service import FindingService
from app.application.repositories import ApplicationRepository
from app.application.report_document_service import ReportDocumentService
from app.core.config import get_settings
from app.infrastructure.database import Database
from app.infrastructure.database.monitor_repository import MonitorRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.schemas.cases import CreateCaseRequest

CASE_TITLE = "E2E Final Closure 调查 " + datetime.now(UTC).strftime("%H%M%S")


async def main() -> int:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite"):
        print(
            "refusing to seed: DATABASE_URL must point at the disposable E2E "
            "SQLite database, not production data",
            file=sys.stderr,
        )
        return 2

    database = Database(settings.database_url)
    await database.create_schema()
    repository = ApplicationRepository(database)
    social = SocialRepository(database)
    monitors = MonitorRepository(database)
    finding_service = FindingService(database, repository)
    report_service = ReportDocumentService(database)

    case = await repository.create_case(
        CreateCaseRequest(
            title=CASE_TITLE,
            topic=CASE_TITLE,
            platforms=["weibo", "zhihu"],
        )
    )
    other_case = await repository.create_case(
        CreateCaseRequest(
            title="E2E 隔离案例", topic="E2E 隔离案例", platforms=["weibo"]
        )
    )

    # ---- Scenario E: 3 semantic posts + 48 fillers = 51 (page_size 50) ----
    base = datetime(2026, 8, 20, 8, tzinfo=UTC)
    posts: list[dict[str, object]] = [
        {
            "platform": "weibo",
            "native_id": "fc-w1",
            # 标题/正文刻意不含“E2E独家”：Scenario E 关键词过滤必须只命中 z1。
            "title": "E2E直击：现场报道",
            "content": "E2E直击内容：事件现场的首发直击报道。",
            "author": "E2E账号A",
            "published_at": (base + timedelta(minutes=10)).isoformat(),
            "url": "https://weibo.com/fc-w1",
        },
        {
            "platform": "weibo",
            "native_id": "fc-w2",
            "title": "转发讨论",
            "content": "针对首发内容的转发与讨论串。",
            "author": "E2E账号B",
            "published_at": (base + timedelta(minutes=30)).isoformat(),
        },
        {
            "platform": "zhihu",
            "native_id": "fc-z1",
            "title": "E2E独家分析",
            "content": "E2E独家：知乎专栏对事件的深度分析文章。",
            "author": "E2E账号C",
            "published_at": (base + timedelta(minutes=60)).isoformat(),
        },
    ]
    for i in range(48):
        posts.append(
            {
                "platform": "weibo",
                "native_id": f"fc-fill-{i:02d}",
                "title": "",
                "content": f"E2E filler 常规讨论 #{i:02d}：日常转发与评论。",
                "author": f"filler{i:02d}",
                "published_at": (base + timedelta(minutes=90 + i)).isoformat(),
            }
        )
    await social.persist_batch(case_id=case.id, posts=posts)
    await social.persist_batch(
        case_id=other_case.id,
        posts=[
            {
                "platform": "weibo",
                "native_id": "fc-other",
                "content": "隔离案例内容，不应出现在主 case 列表。",
            }
        ],
    )
    all_posts = await social.list_posts_by_case(case.id)
    by_native = {post.native_id: post.id for post in all_posts}

    # ---- Scenario A/B: claim + linked/unassigned evidence ----
    run = await repository.create_agent_run(
        case_id=case.id, turn_id=None, objective="E2E seed 分析", metadata={}
    )
    claim = await repository.create_claim(
        case_id=case.id,
        text="E2E 主张：多平台存在协同转发痕迹",
        created_by_run_id=run.id,
    )
    linked_evidence = await repository.create_evidence(
        case_id=case.id,
        claim_id=claim.id,
        source_type="social_post",
        source_id=by_native["fc-w1"],
        stance="support",
        excerpt="E2E 关联证据摘录：首发直击与转发时间线高度一致。",
        relevance=0.9,
        metadata={"platform": "weibo", "author": "E2E账号A"},
    )
    unassigned_evidence = await repository.create_evidence(
        case_id=case.id,
        claim_id=None,
        source_type="social_post",
        source_id=by_native["fc-z1"],
        stance="context",
        excerpt="E2E 未归属证据摘录：知乎深度分析原文片段。",
        relevance=0.5,
        metadata={"platform": "zhihu", "author": "E2E账号C"},
    )

    # ---- Scenario D: propagation nodes + one unreviewed edge ----
    await repository.create_propagation_node(
        case_id=case.id, post_id=by_native["fc-w1"], role="source", score=0.9
    )
    await repository.create_propagation_node(
        case_id=case.id, post_id=by_native["fc-w2"], role="hub", score=0.6
    )
    edge = await repository.create_propagation_edge(
        case_id=case.id,
        source_post_id=by_native["fc-w1"],
        target_post_id=by_native["fc-w2"],
        relation="copy_spread",
        confidence=0.82,
        feature_scores={"text_sim": 0.82},
        evidence_ids=[],
        algorithm_version="e2e-seed",
    )
    assert edge.human_review_state == "unreviewed"

    # ---- Scenario B: candidate finding ----
    finding = await finding_service.create_manual(
        case.id,
        kind="manual",
        title="E2E 协同传播结论",
        statement="E2E 结论：首发与转发账号存在协同传播行为。",
        confidence=0.7,
    )

    # ---- Scenario C: valid / invalid report drafts (via report artifacts) --
    valid_artifact = await repository.create_artifact(
        case_id=case.id,
        run_id=run.id,
        kind="report",
        title="E2E 合规报告 artifact",
        data={
            "title": "E2E 合规调查报告",
            "summary": "引用关系完整，可以通过发布校验。",
            "sections": [{"title": "概述", "content": "E2E 报告正文。"}],
            "citation_links": [{"conclusion": "c1", "finding_id": finding.id}],
        },
    )
    invalid_artifact = await repository.create_artifact(
        case_id=case.id,
        run_id=run.id,
        kind="report",
        title="E2E 违规报告 artifact",
        data={
            "title": "E2E 违规引用报告",
            "summary": "包含不存在的证据引用，发布必须被 gate 拒绝。",
            "sections": [{"title": "概述", "content": "E2E 违规正文。"}],
            "citation_links": [
                {"conclusion": "c1", "evidence_ids": ["ev-nonexistent-e2e"]}
            ],
        },
    )
    # Import invalid first, valid second: the report view picks the most
    # recently updated draft as activeReport, so the E2E starts on the valid
    # one and lands on the invalid draft after publishing the valid report.
    invalid_report = await report_service.import_from_artifact(
        case.id, str(invalid_artifact.id)
    )
    valid_report = await report_service.import_from_artifact(
        case.id, str(valid_artifact.id)
    )

    # ---- Scenario F: signal fixture (monitor + rule + open occurrence) ----
    monitor = await monitors.create_monitor(case_id=case.id, name="E2E 监测")
    rule = await monitors.create_rule(
        monitor_id=monitor.id,
        rule_type="absolute_volume",
        severity="warning",
    )
    occurrence, _created = await monitors.upsert_alert_occurrence(
        monitor_id=monitor.id,
        rule_id=rule.id,
        fingerprint="e2e-final-closure-volume",
        cooldown_bucket=datetime.now(UTC).strftime("%Y%m%d%H"),
        severity="warning",
        explanation="E2E fixture：讨论量达到告警阈值。",
        metric_snapshot={"volume": 999},
        evidence_refs={},
    )

    await database.dispose()

    payload = {
        "case_id": case.id,
        "other_case_id": other_case.id,
        "case_title": case.title,
        "claim_id": claim.id,
        "evidence_id": linked_evidence.id,
        "unassigned_evidence_id": unassigned_evidence.id,
        "propagation_edge_id": edge.id,
        "finding_id": finding.id,
        "valid_report_id": valid_report.id,
        "invalid_report_id": invalid_report.id,
        "signal_id": occurrence.id,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
