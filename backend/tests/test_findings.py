"""M4: Finding 生命周期与 Provenance 测试。

service 层：artifact sync 幂等、opinion/verification materialize、状态机、
Review accepted→verified 原子同步、delete_case 无 orphan。
API 层：findings 路由 + provenance 一跳 + 跨 case 拒绝。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.finding_service import FindingService
from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.infrastructure.database import Database
from app.infrastructure.database.models import (
    FindingEvidenceLinkRecord,
    FindingRecord,
    ReviewDecisionRecord,
    ReviewItemRecord,
)
from app.main import create_app
from app.schemas.cases import CreateCaseRequest


async def _seed(database: Database) -> tuple[ApplicationRepository, FindingService, str]:
    await database.create_schema()
    repository = ApplicationRepository(database)
    service = FindingService(database, repository)
    case = await repository.create_case(
        CreateCaseRequest(topic="Materializer 案例", platforms=["weibo"])
    )
    return repository, service, case.id


async def test_opinion_artifact_materializes_findings(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f1.db'}")
    repository, service, case_id = await _seed(database)
    run = await repository.create_agent_run(
        case_id=case_id, turn_id=None, objective="分析", metadata={}
    )
    artifact = await repository.create_artifact(
        case_id=case_id,
        run_id=run.id,
        kind="opinion_analysis",
        title="观点分析结果",
        data={
            "conclusions": [
                {
                    "claim": "多数声音支持延期开学",
                    "evidence_ids": ["ev-1", "ev-2"],
                    "confidence": 0.82,
                },
                {"claim": "少数质疑补偿方案", "confidence": 0.4},
                {"claim": "", "confidence": 0.9},  # 空 claim 跳过
            ]
        },
    )

    first = await service.sync_from_artifact(artifact)
    assert first == {"created": 2, "skipped": 0}
    # 幂等：重复 sync 不重置、不重复创建
    second = await service.sync_from_artifact(artifact)
    assert second == {"created": 0, "skipped": 2}

    findings = await service.list(case_id)
    assert len(findings) == 2
    by_statement = {item.statement: item for item in findings}
    top = by_statement["多数声音支持延期开学"]
    assert top.kind == "opinion"
    assert top.status == "candidate"
    assert top.confidence == 0.82
    detail = await service.detail(case_id, top.id)
    assert {link.evidence_ref for link in detail["evidence_links"]} == {"ev-1", "ev-2"}
    assert detail["sources"][0].source_type == "artifact"
    assert detail["sources"][0].source_id == artifact.id


async def test_fact_check_artifact_maps_verdict_and_contradicts(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f2.db'}")
    repository, service, case_id = await _seed(database)
    artifact = await repository.create_artifact(
        case_id=case_id,
        run_id=None,
        kind="fact_check",
        title="核查卡",
        data={
            "cards": [
                {
                    "claim": "官方已确认延期",
                    "verdict": "supported",
                    "confidence": 0.9,
                    "supporting_evidence": ["ev-a"],
                    "contradicting_evidence": ["ev-b"],
                }
            ]
        },
    )
    result = await service.sync_from_artifact(artifact)
    assert result == {"created": 1, "skipped": 0}

    finding = (await service.list(case_id))[0]
    assert finding.kind == "verification"
    assert finding.attributes_json.get("verdict") == "supported"
    detail = await service.detail(case_id, finding.id)
    relations = {link.evidence_ref: link.relation for link in detail["evidence_links"]}
    assert relations == {"ev-a": "supports", "ev-b": "contradicts"}
    # verdict 不改变状态：始终 candidate
    assert finding.status == "candidate"


async def test_status_machine_rejects_candidate_to_verified(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f3.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="手工结论")

    with pytest.raises(ApplicationError) as exc:
        await service.update_status(case_id, finding.id, "verified")
    assert exc.value.code == "finding_invalid_transition"

    await service.update_status(case_id, finding.id, "under_review")
    updated = await service.update_status(case_id, finding.id, "verified")
    assert updated.status == "verified"


async def test_cross_case_finding_access_denied(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f4.db'}")
    repository, service, case_id = await _seed(database)
    other = await repository.create_case(
        CreateCaseRequest(topic="其他案例", platforms=["weibo"])
    )
    finding = await service.create_manual(case_id, statement="案例内结论")

    with pytest.raises(ApplicationError) as exc:
        await service.get_for_case(other.id, finding.id)
    assert exc.value.code == "finding_scope_mismatch"


async def test_review_accepted_syncs_finding_verified_atomically(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f5.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="待审核结论")
    await service.update_status(case_id, finding.id, "under_review")

    item = await repository.create_review_item(
        ReviewItemRecord(
            case_id=case_id,
            object_type="finding",
            object_id=finding.id,
            summary=finding.statement,
        )
    )
    # 将 item 置为 in_review（ReviewService.claim 的等效前置）
    item.status = "under_review"
    async with database.session_factory() as session:
        session.add(item)
        await session.commit()
        await session.refresh(item)

    decision = ReviewDecisionRecord(
        item_id=item.id,
        object_version=item.current_version,
        decision="approved",
        reason="核实无误",
        actor="tester",
    )
    result = await repository.decide_review_item(
        item_id=item.id,
        expected_status="under_review",
        expected_version=item.current_version,
        target_status="accepted",
        decision=decision,
    )
    assert result is not None
    # 同一决策事务内：Review accepted → Finding verified
    updated = await service.get_for_case(case_id, finding.id)
    assert updated.status == "verified"


async def test_delete_case_removes_finding_tables(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f6.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(
        case_id, statement="级联删除验证", evidence_links=[("ev-x", "supports")]
    )
    await repository.delete_case(case_id)

    from sqlalchemy import select

    async with database.session_factory() as session:
        remaining = (
            await session.scalars(
                select(FindingRecord).where(FindingRecord.case_id == case_id)
            )
        ).all()
        assert remaining == []
        orphan_links = (
            await session.scalars(
                select(FindingEvidenceLinkRecord).where(
                    FindingEvidenceLinkRecord.finding_id == finding.id
                )
            )
        ).all()
        assert orphan_links == []


def test_findings_api_and_provenance(tmp_path: Path) -> None:
    """API 层：create/list/detail + provenance 一跳 + 跨 case 拒绝。"""
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'f7.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/cases", json={"topic": "API 案例", "platforms": ["weibo"]}
        )
        case_id = created.json()["id"]
        other = client.post(
            "/api/v1/cases", json={"topic": "其他案例", "platforms": ["weibo"]}
        )
        other_id = other.json()["id"]

        finding = client.post(
            f"/api/v1/cases/{case_id}/findings",
            json={"statement": "跨平台传播存在协同痕迹", "confidence": 0.7},
        )
        assert finding.status_code == 201, finding.text
        body = finding.json()
        assert body["status"] == "candidate"
        assert body["attributes"] == {}

        # evidence link 添加 + detail 聚合
        linked = client.post(
            f"/api/v1/cases/{case_id}/findings/{body['id']}/evidence",
            json={"evidence_ref": "ev-77", "relation": "supports"},
        )
        assert linked.status_code == 200
        detail = client.get(f"/api/v1/cases/{case_id}/findings/{body['id']}")
        assert detail.status_code == 200
        detail_body = detail.json()
        assert detail_body["evidence_links"] == [
            {"evidence_ref": "ev-77", "relation": "supports"}
        ]

        # provenance：finding 一跳 upstream 含 evidence
        prov = client.get(f"/api/v1/cases/{case_id}/provenance/finding/{body['id']}")
        assert prov.status_code == 200
        upstream = prov.json()["upstream"]
        assert any(item["id"] == "ev-77" for item in upstream)

        # 跨 case provenance 拒绝（404，不泄露他 case 存在性）
        cross = client.get(f"/api/v1/cases/{other_id}/provenance/finding/{body['id']}")
        assert cross.status_code == 404
        assert cross.json()["code"] == "provenance_object_not_found"

        # 未知类型 → provenance_object_type_unknown（400）
        unknown = client.get(f"/api/v1/cases/{case_id}/provenance/unknown_type/xyz")
        assert unknown.status_code == 400
        assert unknown.json()["code"] == "provenance_object_type_unknown"

        # findings:sync 幂等入口可用（无 artifact 时 created=0）
        synced = client.post(f"/api/v1/cases/{case_id}/findings:sync")
        assert synced.status_code == 200
        assert synced.json()["created"] == 0



