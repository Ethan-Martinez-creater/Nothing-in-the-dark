"""V3 §77: Investigation Quality tests (Q01–Q18).

全部 deterministic（无 LLM），内存 SQLite（tests/memory_db.py）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.application.collection_service import CollectionDefinitionService
from app.application.investigation_quality_service import (
    QUALITY_DISCLAIMER,
    InvestigationQualityService,
)
from app.application.report_document_service import ReportDocumentService
from app.application.repositories import ApplicationRepository
from app.infrastructure.database.collection_run_repository import (
    CollectionRunRepository,
)
from app.infrastructure.database.finding_repository import FindingRepository
from app.infrastructure.database.investigation_quality_repository import (
    InvestigationQualityRepository,
)
from app.infrastructure.database.models import (
    FindingEvidenceLinkRecord,
    FindingRecord,
    ReportDocumentRecord,
)
from app.infrastructure.database.report_repository import ReportDocumentRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.schemas.cases import CreateCaseRequest
from tests.memory_db import MemoryDatabase

FIVE_PLATFORMS = ["weibo", "bilibili", "tieba", "zhihu", "douyin"]


async def _setup() -> Any:
    database = MemoryDatabase()
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(
        CreateCaseRequest(
            topic="华为竹知了事件",
            platforms=["weibo", "zhihu"],
            time_start="2026-08-10",
            time_end="2026-08-20",
        )
    )
    social = SocialRepository(database)
    collection_runs = CollectionRunRepository(database)
    findings_repo = FindingRepository(database)
    quality_repo = InvestigationQualityRepository(database)
    report_repo = ReportDocumentRepository(database)
    report_service = ReportDocumentService(database)
    definitions = CollectionDefinitionService(database, llm=None)
    service = InvestigationQualityService(
        repository=app_repo,
        social_repository=social,
        collection_run_repository=collection_runs,
        finding_repository=findings_repo,
        quality_repository=quality_repo,
        report_document_service=report_service,
        collection_definition_service=definitions,
        database=database,
    )
    from types import SimpleNamespace

    return SimpleNamespace(
        db=database,
        case=case,
        service=service,
        app_repo=app_repo,
        social=social,
        collection_runs=collection_runs,
        findings=findings_repo,
        quality_repo=quality_repo,
        report_repo=report_repo,
        report_service=report_service,
        definitions=definitions,
    )


async def _make_case(env: Any, platforms: list[str]) -> Any:
    return await env.app_repo.create_case(
        CreateCaseRequest(
            topic="跨事件调查",
            platforms=platforms,
            time_start="2026-08-10",
            time_end="2026-08-20",
        )
    )


async def _make_definition(env: Any, case_id: str, platforms: list[str]) -> Any:
    definition = await env.definitions.create_manual(
        case_id, goal="全平台采集", platforms=platforms
    )
    return await env.definitions.activate(case_id, definition.id)


async def _make_terminal_run(
    env: Any,
    case_id: str,
    definition: Any,
    platform_status: dict[str, str],
    *,
    status: str = "completed",
) -> Any:

    run = await env.collection_runs.create(
        case_id=case_id,
        request_fingerprint=f"fp-{case_id}-{datetime.now(UTC).timestamp()}",
        request_json={"platforms": list(platform_status)},
        phase="discovery",
        collection_definition_id=definition.id,
        collection_definition_version=definition.version,
    )
    # update_progress_if_owner 要求 running + lease_owner 匹配
    async with env.db.session_factory() as session:
        record = await session.get(type(run), run.id)
        assert record is not None
        record.status = "running"
        record.lease_owner = "test-worker"
        await session.commit()
    progress = {
        "platforms": {
            platform: {"status": pstatus}
            for platform, pstatus in platform_status.items()
        }
    }
    await env.collection_runs.update_progress_if_owner(
        run.id,
        "test-worker",
        progress_json=progress,
        posts_collected=10,
        comments_collected=0,
    )
    if status == "completed":
        await env.collection_runs.mark_completed_if_owner(run.id, "test-worker", {})
    else:
        await env.collection_runs.mark_completed_with_errors_if_owner(
            run.id, "test-worker", {}
        )
    return run


async def _make_finding(
    env: Any,
    case_id: str,
    *,
    status: str = "candidate",
    links: list[tuple[str, str]] | None = None,
) -> FindingRecord:
    record = FindingRecord(
        case_id=case_id,
        kind="opinion",
        title=f"finding-{status}-{datetime.now(UTC).timestamp()}",
        statement="测试结论陈述",
        status=status,
    )
    record = await env.findings.create(record)
    for evidence_ref, relation in links or []:
        link = FindingEvidenceLinkRecord(
            finding_id=record.id,
            evidence_ref=evidence_ref,
            relation=relation,
        )
        async with env.db.session_factory() as session:
            session.add(link)
            await session.commit()
    return record


async def _make_report(
    env: Any,
    case_id: str,
    *,
    status: str = "draft",
    citation_links: list[Any] | None = None,
) -> ReportDocumentRecord:
    artifact = await env.app_repo.create_artifact(
        case_id=case_id,
        run_id=None,
        kind="report",
        title="报告来源 Artifact",
        data={"title": "测试报告", "summary": "摘要"},
    )
    record = ReportDocumentRecord(
        family_id=f"family-{case_id}-{datetime.now(UTC).timestamp()}",
        case_id=case_id,
        source_artifact_id=artifact.id,
        status=status,
        title="测试报告",
        content_json={
            "title": "测试报告",
            "executive_summary": "摘要",
            "sections": [],
            "citation_links": citation_links or [],
        },
        lock_version=1,
    )
    return await env.report_repo.create(record)


# ---------------------------------------------------------------------------
# Q01–Q04: collection coverage / empty case
# ---------------------------------------------------------------------------


async def test_q01_empty_case_insufficient_data() -> None:
    env = await _setup()
    payload = await env.service.evaluate(env.case.id)
    assert payload["overall_score"] is None
    assert payload["grade"] == "insufficient_data"
    await env.db.dispose()


async def test_q02_collection_five_of_five_scores_100() -> None:
    env = await _setup()
    case = await _make_case(env, FIVE_PLATFORMS)
    definition = await _make_definition(env, case.id, FIVE_PLATFORMS)
    await _make_terminal_run(
        env, case.id, definition, {p: "completed" for p in FIVE_PLATFORMS}
    )
    payload = await env.service.evaluate(case.id)
    collection = next(
        d for d in payload["dimensions"] if d["key"] == "collection_coverage"
    )
    assert collection["score"] == 100.0
    assert collection["metrics"]["missing_platforms"] == []
    await env.db.dispose()


async def test_q03_running_collection_no_premature_critical() -> None:
    env = await _setup()
    case = await _make_case(env, FIVE_PLATFORMS)
    definition = await _make_definition(env, case.id, FIVE_PLATFORMS)
    # queued/running 的匹配 run：未完成平台只能是 info，不得 critical
    run = await env.collection_runs.create(
        case_id=case.id,
        request_fingerprint=f"fp-{case.id}",
        request_json={"platforms": FIVE_PLATFORMS},
        phase="discovery",
        collection_definition_id=definition.id,
        collection_definition_version=definition.version,
    )
    assert run.status == "queued"
    payload = await env.service.evaluate(case.id)
    collection = next(
        d for d in payload["dimensions"] if d["key"] == "collection_coverage"
    )
    assert collection["metrics"]["collection_in_progress"] is True
    codes = {(g["code"], g["severity"]) for g in payload["gaps"]}
    assert ("missing_collection_platform", "critical") not in codes
    assert ("collection_in_progress", "info") in codes
    await env.db.dispose()


async def test_q04_terminal_missing_half_or_more_is_critical() -> None:
    env = await _setup()
    case = await _make_case(env, FIVE_PLATFORMS)
    definition = await _make_definition(env, case.id, FIVE_PLATFORMS)
    statuses = {p: "failed" for p in FIVE_PLATFORMS}
    statuses["weibo"] = "completed"
    statuses["zhihu"] = "completed"
    await _make_terminal_run(
        env, case.id, definition, statuses, status="completed_with_errors"
    )
    payload = await env.service.evaluate(case.id)
    codes = {(g["code"], g["severity"]) for g in payload["gaps"]}
    assert ("missing_collection_platform", "critical") in codes
    await env.db.dispose()


async def test_q04b_terminal_partial_missing_is_warning() -> None:
    env = await _setup()
    case = await _make_case(env, FIVE_PLATFORMS)
    definition = await _make_definition(env, case.id, FIVE_PLATFORMS)
    statuses = {p: "completed" for p in FIVE_PLATFORMS}
    statuses["douyin"] = "failed"
    await _make_terminal_run(
        env, case.id, definition, statuses, status="completed_with_errors"
    )
    payload = await env.service.evaluate(case.id)
    codes = {(g["code"], g["severity"]) for g in payload["gaps"]}
    assert ("missing_collection_platform", "warning") in codes
    await env.db.dispose()


# ---------------------------------------------------------------------------
# Q05–Q08: evidence / finding support / resolution
# ---------------------------------------------------------------------------


async def test_q05_claim_without_evidence_is_warning() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    run_id = "run-" + case.id
    claim = await env.app_repo.create_claim(
        case_id=case.id, text="无证据主张", created_by_run_id=run_id
    )
    supported = await env.app_repo.create_claim(
        case_id=case.id, text="有证据主张", created_by_run_id=run_id
    )
    await env.app_repo.create_evidence(
        case_id=case.id,
        claim_id=supported.id,
        source_type="post",
        source_id="p1",
        stance="supports",
        excerpt="支持内容",
    )
    payload = await env.service.evaluate(case.id)
    evidence_dim = next(
        d for d in payload["dimensions"] if d["key"] == "evidence_coverage"
    )
    assert evidence_dim["score"] == 50.0
    assert evidence_dim["metrics"]["claims_without_evidence_count"] == 1
    codes = {(g["code"], g["severity"]) for g in payload["gaps"]}
    assert ("claim_without_evidence", "warning") in codes
    # warning 最多列出 20 个 claim_id
    warning = next(w for w in payload["warnings"] if w["code"] == "claim_without_evidence")
    assert claim.id in warning["claim_ids"]
    await env.db.dispose()


async def test_q06_only_contradicts_context_not_supported() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    # 只有 contradicts/context link 的 finding 不算 supported（§16）
    finding = await _make_finding(
        env,
        case.id,
        links=[("ev-x", "contradicts"), ("ev-y", "context")],
    )
    assert finding.id
    payload = await env.service.evaluate(case.id)
    support_dim = next(
        d for d in payload["dimensions"] if d["key"] == "finding_support"
    )
    assert support_dim["metrics"]["findings_total"] == 1
    assert support_dim["metrics"]["findings_with_support"] == 0
    assert support_dim["score"] == 0.0
    await env.db.dispose()


async def test_q07_verified_without_supports_link_is_critical() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    await _make_finding(
        env,
        case.id,
        status="verified",
        links=[("ev-x", "contradicts")],  # 有 link 但非 supports → 仍 critical
    )
    payload = await env.service.evaluate(case.id)
    codes = {(g["code"], g["severity"]) for g in payload["gaps"]}
    assert (
        "verified_finding_without_supporting_evidence",
        "critical",
    ) in codes
    await env.db.dispose()


async def test_q08_candidate_finding_not_resolved() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    await _make_finding(env, case.id, status="candidate")
    payload = await env.service.evaluate(case.id)
    resolution = next(
        d for d in payload["dimensions"] if d["key"] == "review_resolution"
    )
    assert resolution["label"] == "Resolution"
    assert resolution["metrics"]["terminal_findings"] == 0
    assert resolution["score"] == 0.0
    await env.db.dispose()


# ---------------------------------------------------------------------------
# Q09–Q11: provenance / report citation / publish validator sharing
# ---------------------------------------------------------------------------


async def test_q09_dangling_provenance_lowers_score() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    await _make_finding(env, case.id, links=[("ev-missing", "supports")])
    payload = await env.service.evaluate(case.id)
    provenance = next(
        d for d in payload["dimensions"] if d["key"] == "provenance_integrity"
    )
    assert provenance["metrics"]["checked_refs"] == 1
    assert provenance["metrics"]["dangling_refs"] == 1
    assert provenance["score"] == 0.0
    assert payload["overall_score"] is not None
    assert payload["overall_score"] < 100
    await env.db.dispose()


async def test_q10_published_report_dangling_citation_critical() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    await _make_report(
        env,
        case.id,
        status="published",
        citation_links=[{"evidence_ids": ["ghost-evidence"]}],
    )
    payload = await env.service.evaluate(case.id)
    codes = {(g["code"], g["severity"]) for g in payload["gaps"]}
    assert ("dangling_report_citation", "critical") in codes
    await env.db.dispose()


async def test_q11_validate_for_publish_shares_gate_validator() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    report = await _make_report(
        env,
        case.id,
        citation_links=[{"evidence_ids": ["ghost-evidence"]}],
    )
    validation = await env.report_service.validate_for_publish(case.id, report.id)
    assert validation["ok"] is False
    assert validation["problems"]
    # 与 publish gate 同一 validator：change_status(published) 抛出的 details
    # 与 validate_for_publish 的 problems 一致
    import pytest

    from app.core.errors import ApplicationError

    with pytest.raises(ApplicationError) as exc_info:
        await env.report_service.change_status(case.id, report.id, "published")
    assert exc_info.value.code == "report_publish_validation_failed"
    assert list(exc_info.value.details or []) == list(validation["problems"])
    # 只读：report 状态不变
    reloaded = await env.report_repo.get(report.id)
    assert reloaded is not None and reloaded.status == "draft"
    await env.db.dispose()


# ---------------------------------------------------------------------------
# Q12: unavailable dimension not scored as zero
# ---------------------------------------------------------------------------


async def test_q12_unavailable_dimension_removed_from_denominator() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    # 只有 evidence_coverage 可用（evidence_total>0 且 claims_total=0 → 100）
    await env.app_repo.create_evidence(
        case_id=case.id,
        claim_id=None,
        source_type="post",
        source_id="p-only",
        stance="context",
        excerpt="未归组证据",
    )
    payload = await env.service.evaluate(case.id)
    assert payload["overall_score"] == 100.0
    assert payload["grade"] == "strong"
    available = [d for d in payload["dimensions"] if d["available"]]
    assert [d["key"] for d in available] == ["evidence_coverage"]
    await env.db.dispose()


# ---------------------------------------------------------------------------
# Q13–Q17: fingerprint cache / invalidation
# ---------------------------------------------------------------------------


async def test_q13_unchanged_fingerprint_returns_cached() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    first = await env.service.evaluate(case.id)
    second = await env.service.evaluate(case.id)
    assert second["input_fingerprint"] == first["input_fingerprint"]
    assert second["computed_at"] == first["computed_at"]
    await env.db.dispose()


async def test_q14_finding_update_changes_fingerprint() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    finding = await _make_finding(env, case.id)
    first = await env.service.evaluate(case.id)
    async with env.db.session_factory() as session:
        record = await session.get(FindingRecord, finding.id)
        assert record is not None
        record.statement = "更新后的结论陈述"
        await session.commit()
    second = await env.service.evaluate(case.id)
    assert second["input_fingerprint"] != first["input_fingerprint"]
    await env.db.dispose()


async def test_q15_evidence_link_mutation_changes_fingerprint() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    finding = await _make_finding(env, case.id)
    first = await env.service.evaluate(case.id)
    await env.findings.add_evidence_link(finding.id, "ev-new", "supports")
    second = await env.service.evaluate(case.id)
    assert second["input_fingerprint"] != first["input_fingerprint"]
    removed = await env.findings.remove_evidence_link(finding.id, "ev-new", "supports")
    assert removed is True
    third = await env.service.evaluate(case.id)
    assert third["input_fingerprint"] != second["input_fingerprint"]
    assert third["input_fingerprint"] == first["input_fingerprint"]
    await env.db.dispose()


async def test_q16_source_link_added_changes_fingerprint() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    finding = await _make_finding(env, case.id)
    first = await env.service.evaluate(case.id)
    await env.findings.create_source_link(finding.id, "artifact", "art-1", "")
    second = await env.service.evaluate(case.id)
    assert second["input_fingerprint"] != first["input_fingerprint"]
    await env.db.dispose()


async def test_q17_report_lock_version_change_changes_fingerprint() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    report = await _make_report(env, case.id)
    first = await env.service.evaluate(case.id)
    await env.report_repo.update_draft(
        report.id,
        expected_lock_version=1,
        title="更新后的报告标题",
    )
    second = await env.service.evaluate(case.id)
    assert second["input_fingerprint"] != first["input_fingerprint"]
    await env.db.dispose()


# ---------------------------------------------------------------------------
# Q18: wording — quality != truth
# ---------------------------------------------------------------------------


async def test_q18_quality_wording_not_truth_score() -> None:
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    payload = await env.service.evaluate(case.id)
    assert payload["disclaimer"] == QUALITY_DISCLAIMER
    assert "不代表事实真实性" in payload["disclaimer"]
    await env.db.dispose()


# ---------------------------------------------------------------------------
# Q19: Home workspace overview aggregate（V3 §44）
# ---------------------------------------------------------------------------


async def test_q19_home_overview_aggregates_quality_attention() -> None:
    """Home 只读持久化 Quality：attention 列表带 Case title，
    无 QualityRecord 的 Case 计入 quality_unassessed_count。"""
    from app.application.signal_service import SignalService
    from app.application.workspace_service import WorkspaceOverviewService
    from app.infrastructure.database.monitor_repository import MonitorRepository

    env = await _setup()
    case_b = await _make_case(env, ["weibo"])
    case_c = await _make_case(env, ["weibo"])
    now = datetime.now(UTC)
    await env.quality_repo.upsert(
        case_id=env.case.id,
        overall_score=91.0,
        grade="strong",
        dimensions={},
        metrics={},
        gaps=[],
        warnings=[],
        input_fingerprint="fp-strong",
        algorithm_version="quality-1.0.0",
        computed_at=now,
    )
    await env.quality_repo.upsert(
        case_id=case_b.id,
        overall_score=42.0,
        grade="weak",
        dimensions={},
        metrics={},
        gaps=[],
        warnings=[],
        input_fingerprint="fp-weak",
        algorithm_version="quality-1.0.0",
        computed_at=now,
    )

    overview_service = WorkspaceOverviewService(
        env.db,
        SignalService(env.db, MonitorRepository(env.db)),
        quality_repository=env.quality_repo,
    )
    payload = await overview_service.overview()

    attention = payload.investigations_needing_attention
    assert [entry.case_id for entry in attention] == [case_b.id]
    assert attention[0].grade == "weak"
    assert attention[0].overall_score == 42.0
    # title 来自 Case join，而不是 case_id 兜底
    assert attention[0].title == case_b.title
    # case_c 完全未评估 → unassessed=1（strong/weak 已评估）
    assert payload.quality_unassessed_count == 1
    await env.db.dispose()


# ---------------------------------------------------------------------------
# Q20: provenance checked_refs 分母修复（Rework R8）
# ---------------------------------------------------------------------------


async def test_q20_citation_inspection_keeps_valid_refs_in_denominator() -> None:
    """10 refs / 9 valid / 1 invalid → checked_refs=10, dangling=1, score=90。

    修复前 dangling 被同时计入分母与分子（checked=1, dangling=1 → 0%）。
    """
    env = await _setup()
    case = await _make_case(env, ["weibo"])
    citation_links = (
        [{"ref": "aggregate_social_data:platform"}]
        + [{"ref": "aggregate_social_data:day"}]
        + [{"ref": "aggregate_social_data:content_type"}]
        + [{"ref": "aggregate_social_data:platform"}]
        + [{"ref": "aggregate_social_data:day"}]
        + [{"ref": "aggregate_social_data:content_type"}]
        + [{"ref": "aggregate_social_data:platform"}]
        + [{"ref": "aggregate_social_data:day"}]
        + [{"ref": "aggregate_social_data:content_type"}]
        + [{"evidence_ids": ["ghost-evidence"]}]
    )
    assert len(citation_links) == 10
    report = await _make_report(
        env, case.id, status="draft", citation_links=citation_links
    )
    payload = await env.service.evaluate(case.id)
    provenance = next(
        d for d in payload["dimensions"] if d["key"] == "provenance_integrity"
    )
    assert provenance["metrics"]["checked_refs"] == 10
    assert provenance["metrics"]["dangling_refs"] == 1
    assert provenance["score"] == 90.0
    # inspection 入口本身也返回一致结果
    inspection = await env.report_service.inspect_citation_links(
        case.id,
        report.content_json["citation_links"],
    )
    assert inspection["checked_refs"] == 10
    assert len(inspection["problems"]) == 1
    await env.db.dispose()
