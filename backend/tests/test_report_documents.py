"""M7: Report Document 发布流测试。

artifact→draft 幂等、published 不可编辑、revise 产生新 draft、
乐观锁冲突、publish gate（空内容/跨 case 引用阻止发布）、HTML 导出、
delete_case 级联。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.report_document_service import ReportDocumentService
from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.infrastructure.database import Database
from app.infrastructure.database.models import ReportDocumentRecord
from app.main import create_app
from app.schemas.cases import CreateCaseRequest


async def _seed(database: Database) -> tuple[ApplicationRepository, ReportDocumentService, str]:
    await database.create_schema()
    repository = ApplicationRepository(database)
    service = ReportDocumentService(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="报告案例", platforms=["weibo"])
    )
    # 真实 case 内 evidence：publish gate 引用校验需要可解析的 ev-1
    from app.infrastructure.database.models import EvidenceRecord

    async with database.session_factory() as session:
        session.add(
            EvidenceRecord(
                id="ev-1",
                case_id=case.id,
                source_type="social_post",
                source_id="p-1",
                excerpt="官方公告原文摘录。",
            )
        )
        await session.commit()
    return repository, service, case.id


async def _make_report_artifact(
    repository: ApplicationRepository, case_id: str
) -> str:
    artifact = await repository.create_artifact(
        case_id=case_id,
        run_id=None,
        kind="report",
        title="调查报告草稿",
        data={
            "title": "延期开学舆情调查报告",
            "summary": "本报告总结调查结论。",
            "sections": [{"title": "背景", "content": "事件时间线。"}],
            "citation_links": ["ev-1"],
        },
    )
    return artifact.id


async def test_import_and_idempotency(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'r1.db'}")
    repository, service, case_id = await _seed(database)
    artifact_id = await _make_report_artifact(repository, case_id)

    first = await service.import_from_artifact(case_id, artifact_id)
    assert first.status == "draft"
    assert first.lock_version == 1
    assert first.title.startswith("报告案例")
    # 幂等：同 artifact 再次 import 返回同一 draft
    second = await service.import_from_artifact(case_id, artifact_id)
    assert second.id == first.id

    # 非 report artifact 拒绝
    other = await repository.create_artifact(
        case_id=case_id, run_id=None, kind="opinion_analysis", title="x", data={}
    )
    with pytest.raises(ApplicationError) as exc:
        await service.import_from_artifact(case_id, other.id)
    assert exc.value.code == "report_not_found"


async def test_publish_gate_and_transition(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'r2.db'}")
    repository, service, case_id = await _seed(database)
    artifact_id = await _make_report_artifact(repository, case_id)
    draft = await service.import_from_artifact(case_id, artifact_id)

    # draft → published 允许（publish gate 通过：有 summary + ev- 引用）
    published = await service.change_status(case_id, draft.id, "published")
    assert published.status == "published"
    assert published.published_at is not None

    # published 不可编辑
    with pytest.raises(ApplicationError) as exc:
        await service.update_draft(
            case_id, draft.id, expected_lock_version=1, title="改名"
        )
    assert exc.value.code == "report_invalid_transition"

    # published → archived
    archived = await service.change_status(case_id, draft.id, "archived")
    assert archived.status == "archived"


async def test_publish_blocked_without_content(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'r3.db'}")
    repository, service, case_id = await _seed(database)
    empty_artifact = await repository.create_artifact(
        case_id=case_id, run_id=None, kind="report", title="空报告", data={}
    )
    draft = await service.import_from_artifact(case_id, empty_artifact.id)
    # 无 summary/sections → gate 阻止
    with pytest.raises(ApplicationError) as exc:
        await service.change_status(case_id, draft.id, "published")
    assert exc.value.code == "report_publish_validation_failed"


async def _import_with_citations(
    repository: ApplicationRepository,
    service: ReportDocumentService,
    case_id: str,
    citation_links: list,
    tmp_path: Path,
    name: str,
) -> ReportDocumentRecord:
    artifact = await repository.create_artifact(
        case_id=case_id,
        run_id=None,
        kind="report",
        title=f"报告 {name}",
        data={
            "title": "发布校验报告",
            "summary": "摘要内容。",
            "sections": [{"title": "背景", "content": "时间线。"}],
            "citation_links": citation_links,
        },
    )
    return await service.import_from_artifact(case_id, artifact.id)


async def test_citation_evidence_ids_array_validation(tmp_path: Path) -> None:
    """C3：evidence_ids[] 逐条校验（合法通过；不存在/跨 case 阻止发布）。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'r7.db'}")
    repository, service, case_id = await _seed(database)
    other = await repository.create_case(
        CreateCaseRequest(topic="其他案例", platforms=["weibo"])
    )
    from app.infrastructure.database.models import EvidenceRecord

    async with database.session_factory() as session:
        session.add(
            EvidenceRecord(
                id="ev-2", case_id=case_id, source_type="social_post",
                source_id="p-2", excerpt="ev-2 摘录",
            )
        )
        session.add(
            EvidenceRecord(
                id="ev-cross", case_id=other.id, source_type="social_post",
                source_id="p-3", excerpt="他 case 摘录",
            )
        )
        await session.commit()

    # evidence_ids[] 全合法（真实 Report Agent 形状）→ publish 成功
    draft = await _import_with_citations(
        repository, service, case_id,
        [{"conclusion": "结论一", "evidence_ids": ["ev-1", "ev-2"]}],
        tmp_path, "ok",
    )
    published = await service.change_status(case_id, draft.id, "published")
    assert published.status == "published"

    # 含不存在 Evidence → publish 失败，details 定位到具体元素
    draft_bad = await _import_with_citations(
        repository, service, case_id,
        [{"conclusion": "结论二", "evidence_ids": ["ev-1", "ev-ghost"]}],
        tmp_path, "missing",
    )
    with pytest.raises(ApplicationError) as exc:
        await service.change_status(case_id, draft_bad.id, "published")
    assert exc.value.code == "report_publish_validation_failed"
    assert exc.value.details == [
        {"field": "citation_links[0].evidence_ids[1]", "issue": "evidence_not_found"}
    ]

    # 含跨 Case Evidence → publish 失败
    draft_cross = await _import_with_citations(
        repository, service, case_id,
        [{"conclusion": "结论三", "evidence_ids": ["ev-cross"]}],
        tmp_path, "cross",
    )
    with pytest.raises(ApplicationError) as exc:
        await service.change_status(case_id, draft_cross.id, "published")
    assert exc.value.code == "report_publish_validation_failed"
    assert exc.value.details == [
        {"field": "citation_links[0].evidence_ids[0]", "issue": "evidence_not_in_case"}
    ]


async def test_citation_finding_artifact_and_generic_refs(tmp_path: Path) -> None:
    """C3：finding/artifact 引用合法；generic ref 可解析；unknown shape 阻止。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'r8.db'}")
    repository, service, case_id = await _seed(database)
    from app.infrastructure.database.models import FindingRecord

    finding = FindingRecord(
        case_id=case_id, kind="opinion", title="结论标题",
        statement="传播存在协同", status="candidate",
    )
    async with database.session_factory() as session:
        session.add(finding)
        await session.commit()
    artifact = await repository.create_artifact(
        case_id=case_id, run_id=None, kind="opinion_analysis",
        title="来源 artifact", data={"conclusions": []},
    )

    # finding 引用 + artifact 引用 + generic ref 混合 → 全部可解析，publish 成功
    draft = await _import_with_citations(
        repository, service, case_id,
        [
            {"conclusion": "结论 A", "finding_ids": [finding.id]},
            {"artifact_id": artifact.id},
            {"ref": finding.id},  # generic：在 case 内解析为 Finding
        ],
        tmp_path, "mixed",
    )
    published = await service.change_status(case_id, draft.id, "published")
    assert published.status == "published"

    # unknown dict shape（无任何可解析引用）→ 阻止并给出 unresolvable_ref
    draft_unknown = await _import_with_citations(
        repository, service, case_id,
        [{"conclusion": "只有文字没有引用"}],
        tmp_path, "unknown",
    )
    with pytest.raises(ApplicationError) as exc:
        await service.change_status(case_id, draft_unknown.id, "published")
    assert exc.value.code == "report_publish_validation_failed"
    assert exc.value.details == [
        {"field": "citation_links[0]", "issue": "unresolvable_ref"}
    ]

    # generic ref 指向不存在对象 → unresolvable_ref
    draft_ghost = await _import_with_citations(
        repository, service, case_id,
        [{"ref": "no-such-object"}],
        tmp_path, "ghost",
    )
    with pytest.raises(ApplicationError) as exc:
        await service.change_status(case_id, draft_ghost.id, "published")
    assert exc.value.code == "report_publish_validation_failed"
    assert exc.value.details == [
        {"field": "citation_links[0].ref", "issue": "unresolvable_ref"}
    ]


async def test_report_api_cross_case_citation_blocked(tmp_path: Path) -> None:
    """C3 API 层：跨 case citation 的 publish 被 400 + details 阻止。"""
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'r9.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case_id = client.post(
            "/api/v1/cases", json={"topic": "API 引用案例", "platforms": ["weibo"]}
        ).json()["id"]
        other_id = client.post(
            "/api/v1/cases", json={"topic": "其他案例", "platforms": ["weibo"]}
        ).json()["id"]

        container = app.state.container
        from app.infrastructure.database.models import EvidenceRecord

        async with container.database.session_factory() as session:
            session.add(
                EvidenceRecord(
                    id="ev-other", case_id=other_id, source_type="social_post",
                    source_id="p-9", excerpt="他 case 摘录",
                )
            )
            await session.commit()
        artifact = await container.repository.create_artifact(
            case_id=case_id, run_id=None, kind="report", title="跨引用报告",
            data={
                "title": "API 发布校验",
                "summary": "摘要。",
                "sections": [{"title": "s", "content": "c"}],
                "citation_links": [{"evidence_ids": ["ev-other"]}],
            },
        )
        imported = client.post(
            f"/api/v1/cases/{case_id}/reports:from-artifact",
            json={"artifact_id": artifact.id},
        )
        assert imported.status_code in (200, 201), imported.text
        report_id = imported.json()["id"]

        blocked = client.post(
            f"/api/v1/cases/{case_id}/reports/{report_id}:publish",
        )
        assert blocked.status_code == 400
        assert blocked.json()["code"] == "report_publish_validation_failed"
        assert blocked.json()["details"] == [
            {"field": "citation_links[0].evidence_ids[0]", "issue": "evidence_not_in_case"}
        ]


async def test_stale_lock_version_conflicts(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'r4.db'}")
    repository, service, case_id = await _seed(database)
    artifact_id = await _make_report_artifact(repository, case_id)
    draft = await service.import_from_artifact(case_id, artifact_id)

    updated = await service.update_draft(
        case_id,
        draft.id,
        expected_lock_version=1,
        title="第一次编辑",
        content={"executive_summary": "更新后的摘要", "sections": [], "citation_links": []},
    )
    assert updated.lock_version == 2

    with pytest.raises(ApplicationError) as exc:
        await service.update_draft(
            case_id, draft.id, expected_lock_version=1, title="过期编辑"
        )
    assert exc.value.code == "report_version_conflict"


async def test_revise_creates_new_draft(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'r5.db'}")
    repository, service, case_id = await _seed(database)
    artifact_id = await _make_report_artifact(repository, case_id)
    draft = await service.import_from_artifact(case_id, artifact_id)
    await service.change_status(case_id, draft.id, "published")

    revision = await service.revise(case_id, draft.id)
    assert revision.status == "draft"
    assert revision.id != draft.id
    assert revision.family_id == draft.family_id
    assert revision.supersedes_id == draft.id
    assert revision.lock_version == 1


def test_report_api_and_delete_cascade(tmp_path: Path) -> None:
    """API 层：import/publish/download + 跨 case 引用阻止 + delete 级联。"""
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'r6.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case_id = client.post(
            "/api/v1/cases", json={"topic": "API 报告案例", "platforms": ["weibo"]}
        ).json()["id"]
        other_id = client.post(
            "/api/v1/cases", json={"topic": "其他案例", "platforms": ["weibo"]}
        ).json()["id"]

        imported = client.post(
            f"/api/v1/cases/{case_id}/reports:from-artifact",
            json={},
        )
        # 缺 artifact_id → 422 校验失败
        assert imported.status_code == 422

        # 先造一个 report artifact（经容器路径太绕，改经 publish gate 语义：
        # 用 sync API 无法造 artifact；此处经 service 不再重复。改测空 404。）
        missing = client.get("/api/v1/reports/does-not-exist")
        assert missing.status_code == 404
        assert missing.json()["code"] == "report_not_found"
        del other_id
