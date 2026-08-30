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


async def _seed_evidence(database: Database, case_id: str, *evidence_ids: str) -> None:
    """在 case 内插入真实 EvidenceRecord（C2：link 只认数据库中的 Evidence）。"""
    from app.infrastructure.database.models import EvidenceRecord

    async with database.session_factory() as session:
        for evidence_id in evidence_ids:
            session.add(
                EvidenceRecord(
                    id=evidence_id,
                    case_id=case_id,
                    source_type="social_post",
                    source_id=f"post-{evidence_id}",
                    excerpt=f"{evidence_id} 摘录",
                )
            )
        await session.commit()


async def _count_rows(database: Database) -> tuple[int, int, int]:
    """FC2 残留断言用：findings / source links / evidence links 总数。"""
    from sqlalchemy import func, select

    from app.infrastructure.database.models import FindingSourceLinkRecord

    async with database.session_factory() as session:
        findings = (await session.scalar(select(func.count()).select_from(FindingRecord))) or 0
        sources = (
            await session.scalar(
                select(func.count()).select_from(FindingSourceLinkRecord)
            )
        ) or 0
        links = (
            await session.scalar(
                select(func.count()).select_from(FindingEvidenceLinkRecord)
            )
        ) or 0
    return findings, sources, links


async def test_create_manual_missing_evidence_leaves_no_partial_write(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fc2a.db'}")
    repository, service, case_id = await _seed(database)
    await _seed_evidence(database, case_id, "ev-real")
    before = await _count_rows(database)

    with pytest.raises(ApplicationError) as excinfo:
        await service.create_manual(
            case_id,
            statement="不存在证据的结论",
            source_type="social_post",
            source_id="post-1",
            evidence_links=[("ev-real", "supports"), ("ev-missing", "supports")],
        )

    assert excinfo.value.code == "finding_evidence_not_found"
    # 0 partial write：finding / source link / evidence link 均未落库
    assert await _count_rows(database) == before


async def test_create_manual_cross_case_evidence_leaves_no_partial_write(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fc2b.db'}")
    repository, service, case_id = await _seed(database)
    other = await repository.create_case(
        CreateCaseRequest(topic="其他案例", platforms=["weibo"])
    )
    await _seed_evidence(database, other.id, "ev-other")
    await _seed_evidence(database, case_id, "ev-real")
    before = await _count_rows(database)

    with pytest.raises(ApplicationError) as excinfo:
        await service.create_manual(
            case_id,
            statement="跨 case 证据的结论",
            evidence_links=[("ev-real", "supports"), ("ev-other", "context")],
        )

    assert excinfo.value.code == "finding_evidence_scope_mismatch"
    assert await _count_rows(database) == before


async def test_create_manual_invalid_relation_leaves_no_partial_write(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fc2c.db'}")
    repository, service, case_id = await _seed(database)
    await _seed_evidence(database, case_id, "ev-real")
    before = await _count_rows(database)

    with pytest.raises(ApplicationError) as excinfo:
        await service.create_manual(
            case_id,
            statement="非法 relation 的结论",
            evidence_links=[("ev-real", "refutes")],
        )

    assert excinfo.value.code == "finding_evidence_invalid"
    assert await _count_rows(database) == before


async def test_create_manual_duplicate_link_rolls_back_whole_transaction(
    tmp_path: Path,
) -> None:
    """前置校验全部通过后，第二条 link 在数据库层撞唯一键：整个事务回滚。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fc2d.db'}")
    repository, service, case_id = await _seed(database)
    await _seed_evidence(database, case_id, "ev-real")
    before = await _count_rows(database)

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await service.create_manual(
            case_id,
            statement="重复 evidence link 的结论",
            evidence_links=[("ev-real", "supports"), ("ev-real", "supports")],
        )

    assert await _count_rows(database) == before


async def test_repository_create_with_links_rolls_back_on_db_error(
    tmp_path: Path,
) -> None:
    """repository atomic helper 自身：DB 异常时 Finding/links 全部回滚。"""
    from sqlalchemy.exc import IntegrityError

    from app.infrastructure.database.models import FindingRecord

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fc2e.db'}")
    repository, service, case_id = await _seed(database)
    await _seed_evidence(database, case_id, "ev-real")
    before = await _count_rows(database)

    record = FindingRecord(
        case_id=case_id,
        kind="manual",
        title="repo 级回滚",
        statement="repo 级回滚结论",
        status="candidate",
        attributes_json={},
    )
    with pytest.raises(IntegrityError):
        await service._findings.create_with_links(
            record,
            source_link=("social_post", "post-9", ""),
            evidence_links=[("ev-real", "supports"), ("ev-real", "supports")],
        )

    assert await _count_rows(database) == before


async def test_create_manual_with_source_and_links_succeeds(tmp_path: Path) -> None:
    """正常创建：Finding + source + 多 Evidence links 一次成功，数据完整。"""
    from app.infrastructure.database.models import FindingSourceLinkRecord

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fc2f.db'}")
    repository, service, case_id = await _seed(database)
    await _seed_evidence(database, case_id, "ev-1", "ev-2", "ev-3")

    record = await service.create_manual(
        case_id,
        kind="manual",
        title="手动结论",
        statement="结论语句",
        confidence=0.7,
        source_type="social_post",
        source_id="post-x",
        source_path="timeline",
        evidence_links=[
            ("ev-1", "supports"),
            ("ev-2", "contradicts"),
            ("ev-3", "context"),
        ],
    )

    findings, sources, links = await _count_rows(database)
    assert findings == 1
    assert sources == 1
    assert links == 3
    stored = await service.get_for_case(case_id, record.id)
    assert stored.status == "candidate"
    assert stored.confidence == 0.7
    source_links = await service._findings.list_source_links(record.id)
    assert len(source_links) == 1
    assert source_links[0].source_id == "post-x"
    evidence_links = await service._findings.list_evidence_links(record.id)
    assert {(link.evidence_ref, link.relation) for link in evidence_links} == {
        ("ev-1", "supports"),
        ("ev-2", "contradicts"),
        ("ev-3", "context"),
    }


async def test_opinion_artifact_materializes_findings(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f1.db'}")
    repository, service, case_id = await _seed(database)
    await _seed_evidence(database, case_id, "ev-1", "ev-2")
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
    assert first == {"created": 2, "skipped": 0, "warnings": []}
    # 幂等：重复 sync 不重置、不重复创建、不重复 link
    second = await service.sync_from_artifact(artifact)
    assert second == {"created": 0, "skipped": 2, "warnings": []}

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
    """混合合法/非法 Evidence：只保存合法 link，坏引用返回 warning（C2）。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f2.db'}")
    repository, service, case_id = await _seed(database)
    await _seed_evidence(database, case_id, "ev-a")  # ev-b 故意不 seed
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
                    "supporting_evidence": ["ev-a", "ev-missing"],
                    "contradicting_evidence": ["ev-b"],
                }
            ]
        },
    )
    result = await service.sync_from_artifact(artifact)
    assert result["created"] == 1
    assert result["skipped"] == 0
    # Finding 照常物化，无效引用逐条 warning，不阻断
    warning_refs = {item["evidence_ref"] for item in result["warnings"]}
    assert warning_refs == {"ev-missing", "ev-b"}
    assert all(
        item["type"] == "invalid_evidence_ref"
        and item["artifact_id"] == str(artifact.id)
        and item["finding_source_path"] == "cards[0]"
        for item in result["warnings"]
    )

    finding = (await service.list(case_id))[0]
    assert finding.kind == "verification"
    assert finding.attributes_json.get("verdict") == "supported"
    detail = await service.detail(case_id, finding.id)
    relations = {link.evidence_ref: link.relation for link in detail["evidence_links"]}
    # 只保存真实存在的合法 link
    assert relations == {"ev-a": "supports"}
    # verdict 不改变状态：始终 candidate
    assert finding.status == "candidate"


async def _seed_review_item(
    repository: ApplicationRepository,
    database: Database,
    case_id: str,
    finding: FindingRecord,
) -> ReviewItemRecord:
    """创建 finding Review item 并置为 under_review（claim 等效前置）。"""
    item = await repository.create_review_item(
        ReviewItemRecord(
            case_id=case_id,
            object_type="finding",
            object_id=finding.id,
            summary=finding.statement,
        )
    )
    item.status = "under_review"
    async with database.session_factory() as session:
        session.add(item)
        await session.commit()
        await session.refresh(item)
    return item


async def test_status_machine_blocks_review_only_statuses(tmp_path: Path) -> None:
    """普通 Finding API 无法产生 verified/rejected（C1：Review 唯一裁决）。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f3.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="手工结论")

    # candidate -> verified 直接失败
    with pytest.raises(ApplicationError) as exc:
        await service.update_status(case_id, finding.id, "verified")
    assert exc.value.code == "finding_review_required"

    # candidate -> under_review -> verified 仍失败
    await service.update_status(case_id, finding.id, "under_review")
    with pytest.raises(ApplicationError) as exc:
        await service.update_status(case_id, finding.id, "verified")
    assert exc.value.code == "finding_review_required"

    # candidate -> under_review -> rejected 同样失败
    with pytest.raises(ApplicationError) as exc:
        await service.update_status(case_id, finding.id, "rejected")
    assert exc.value.code == "finding_review_required"

    # 合法迁移不受影响：under_review -> candidate 回退
    reverted = await service.update_status(case_id, finding.id, "candidate")
    assert reverted.status == "candidate"


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


async def test_manual_evidence_link_requires_real_case_evidence(tmp_path: Path) -> None:
    """C2：手动 link fail closed —— 不存在/跨 case 的 Evidence 都被拒绝。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f11.db'}")
    repository, service, case_id = await _seed(database)
    other = await repository.create_case(
        CreateCaseRequest(topic="另一个案例", platforms=["weibo"])
    )
    await _seed_evidence(database, other.id, "ev-other")
    finding = await service.create_manual(case_id, statement="需要证据支撑")

    # 不存在 → finding_evidence_not_found
    with pytest.raises(ApplicationError) as exc:
        await service.add_evidence_link(case_id, finding.id, "ev-nope", "supports")
    assert exc.value.code == "finding_evidence_not_found"

    # 跨 case → finding_evidence_scope_mismatch
    with pytest.raises(ApplicationError) as exc:
        await service.add_evidence_link(case_id, finding.id, "ev-other", "supports")
    assert exc.value.code == "finding_evidence_scope_mismatch"

    # 手动创建时混入非法引用同样整体拒绝（fail closed）
    await _seed_evidence(database, case_id, "ev-real")
    with pytest.raises(ApplicationError) as exc:
        await service.create_manual(
            case_id,
            statement="混合引用创建",
            evidence_links=[("ev-real", "supports"), ("ev-nope", "context")],
        )
    assert exc.value.code == "finding_evidence_not_found"

    # 真实同 case Evidence → 成功
    updated = await service.add_evidence_link(case_id, finding.id, "ev-real", "supports")
    detail = await service.detail(case_id, finding.id)
    assert [link.evidence_ref for link in detail["evidence_links"]] == ["ev-real"]


async def test_review_accepted_syncs_finding_verified_atomically(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f5.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="待审核结论")
    await service.update_status(case_id, finding.id, "under_review")

    item = await _seed_review_item(repository, database, case_id, finding)

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


async def test_review_rejected_syncs_finding_rejected(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f8.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="存疑结论")
    await service.update_status(case_id, finding.id, "under_review")

    item = await _seed_review_item(repository, database, case_id, finding)
    decision = ReviewDecisionRecord(
        item_id=item.id,
        object_version=item.current_version,
        decision="rejected",
        reason="证据不足且与来源矛盾",
        actor="tester",
    )
    result = await repository.decide_review_item(
        item_id=item.id,
        expected_status="under_review",
        expected_version=item.current_version,
        target_status="rejected",
        decision=decision,
    )
    assert result is not None
    updated = await service.get_for_case(case_id, finding.id)
    assert updated.status == "rejected"


async def test_review_conflict_keeps_finding_status(tmp_path: Path) -> None:
    """Review 决策冲突（乐观锁不匹配）时整个事务回滚，Finding 状态不变。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f9.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="冲突场景结论")
    await service.update_status(case_id, finding.id, "under_review")

    item = await _seed_review_item(repository, database, case_id, finding)
    stale_version = item.current_version + 1  # 过期版本，必然冲突
    decision = ReviewDecisionRecord(
        item_id=item.id,
        object_version=stale_version,
        decision="approved",
        reason="过期请求",
        actor="tester",
    )
    result = await repository.decide_review_item(
        item_id=item.id,
        expected_status="under_review",
        expected_version=stale_version,
        target_status="accepted",
        decision=decision,
    )
    assert result is None
    updated = await service.get_for_case(case_id, finding.id)
    assert updated.status == "under_review"


async def test_verified_finding_reopens_via_under_review_only(tmp_path: Path) -> None:
    """已 verified Finding 可重新进入 under_review，但再次 verified 仍需 Review。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f10.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="复核场景结论")
    await service.update_status(case_id, finding.id, "under_review")
    item = await _seed_review_item(repository, database, case_id, finding)
    result = await repository.decide_review_item(
        item_id=item.id,
        expected_status="under_review",
        expected_version=item.current_version,
        target_status="accepted",
        decision=ReviewDecisionRecord(
            item_id=item.id,
            object_version=item.current_version,
            decision="approved",
            reason="首审通过",
            actor="tester",
        ),
    )
    assert result is not None
    assert (await service.get_for_case(case_id, finding.id)).status == "verified"

    # 重新提交复审：verified -> under_review 合法
    reopened = await service.update_status(case_id, finding.id, "under_review")
    assert reopened.status == "under_review"

    # 再次 verified 仍被普通 API 拒绝
    with pytest.raises(ApplicationError) as exc:
        await service.update_status(case_id, finding.id, "verified")
    assert exc.value.code == "finding_review_required"


async def test_delete_case_removes_finding_tables(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'f6.db'}")
    repository, service, case_id = await _seed(database)
    await _seed_evidence(database, case_id, "ev-x")
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


async def test_findings_api_and_provenance(tmp_path: Path) -> None:
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

        # C2：link 只接受真实存在的 case 内 Evidence —— seed ev-77
        await _seed_evidence(app.state.container.database, case_id, "ev-77")

        # evidence link 添加 + detail 聚合
        linked = client.post(
            f"/api/v1/cases/{case_id}/findings/{body['id']}/evidence",
            json={"evidence_ref": "ev-77", "relation": "supports"},
        )
        assert linked.status_code == 200

        # 不存在的 Evidence 引用被拒绝（fail closed）
        bad_link = client.post(
            f"/api/v1/cases/{case_id}/findings/{body['id']}/evidence",
            json={"evidence_ref": "ev-ghost", "relation": "supports"},
        )
        assert bad_link.status_code == 400
        assert bad_link.json()["code"] == "finding_evidence_not_found"

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

        # C1：Schema 收窄 —— 普通 API 无法请求终审态（422），under_review 合法
        review_only = client.post(
            f"/api/v1/cases/{case_id}/findings/{body['id']}/status",
            json={"status": "verified"},
        )
        assert review_only.status_code == 422
        allowed = client.post(
            f"/api/v1/cases/{case_id}/findings/{body['id']}/status",
            json={"status": "under_review"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["status"] == "under_review"



