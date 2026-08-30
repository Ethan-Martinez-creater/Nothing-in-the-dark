"""M4: Finding 生命周期与 Provenance 测试。

service 层：artifact sync 幂等、opinion/verification materialize、状态机、
Review accepted→verified 原子同步、delete_case 无 orphan。
API 层：findings 路由 + provenance 一跳 + 跨 case 拒绝。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

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
    """返回该 Finding 的 Review item 并置于 under_review（claim 等效前置）。

    FC5 起 ``update_status(under_review)`` 会幂等创建 Review item，这里不再
    重复插入（(case_id, object_type, object_id) 唯一），只取既有 item 改状态。
    """
    items = await repository.list_review_items(
        case_id, object_type="finding", limit=100
    )
    item = next(i for i in items if i.object_id == finding.id)
    async with database.session_factory() as session:
        await session.execute(
            update(ReviewItemRecord)
            .where(ReviewItemRecord.id == item.id)
            .values(status="under_review")
        )
        await session.commit()
    item.status = "under_review"
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


# ================= Post-Closure Correctness Patch 专项测试 =================
# PC1: Finding → Review 原子提交；PC2B: Workbench 重开原子；PC4: 回归。


async def _count_finding_review_items(
    database: Database, case_id: str, finding_id: str
) -> int:
    from sqlalchemy import func, select

    async with database.session_factory() as session:
        count = (
            await session.scalar(
                select(func.count())
                .select_from(ReviewItemRecord)
                .where(
                    ReviewItemRecord.case_id == case_id,
                    ReviewItemRecord.object_type == "finding",
                    ReviewItemRecord.object_id == finding_id,
                )
            )
        ) or 0
    return int(count)


async def _approve_finding_through_review(
    database: Database,
    repository: ApplicationRepository,
    service: FindingService,
    case_id: str,
    finding: FindingRecord,
) -> tuple[ReviewItemRecord, FindingRecord]:
    """真实流程：candidate → submit → approve → Finding verified / item accepted。"""
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
    item, _decision = result
    updated = await service.get_for_case(case_id, finding.id)
    return item, updated


async def test_pc41_first_submit_creates_review_item_atomically(
    tmp_path: Path,
) -> None:
    """PC4.1: candidate 首次提交 → 同一事务内 under_review + unreviewed item。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc41.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="首次提交的结论")

    updated = await service.update_status(case_id, finding.id, "under_review")

    assert updated.status == "under_review"
    assert await _count_finding_review_items(database, case_id, finding.id) == 1
    items = await repository.list_review_items(
        case_id, object_type="finding", limit=100
    )
    item = next(i for i in items if i.object_id == finding.id)
    assert item.status == "unreviewed"
    assert item.summary == "首次提交的结论"


async def test_pc42_duplicate_submit_is_idempotent(tmp_path: Path) -> None:
    """PC4.2: 重复调用原子方法两次 → 1 个 item，无 IntegrityError。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc42.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="幂等提交的结论")

    first_finding, first_item = await repository.submit_finding_for_review(
        case_id=case_id, finding_id=finding.id, summary=finding.statement
    )
    second_finding, second_item = await repository.submit_finding_for_review(
        case_id=case_id, finding_id=finding.id, summary=finding.statement
    )

    assert first_finding.status == "under_review"
    assert second_finding.status == "under_review"
    assert first_item.id == second_item.id
    assert await _count_finding_review_items(database, case_id, finding.id) == 1


async def test_pc43_review_write_failure_rolls_back_whole_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PC4.3: ReviewItem 写入失败 → Finding 也不得改变（0 partial write）。

    通过让 commit 抛 SQLAlchemyError 模拟数据库写入失败，随后必须能从
    数据库证明 Finding 仍为 candidate 且无 ReviewItem。
    """
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.ext.asyncio import AsyncSession

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc43.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="事务回滚结论")

    real_commit = AsyncSession.commit

    async def failing_commit(self: AsyncSession) -> None:
        raise SQLAlchemyError("simulated review write failure")

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    with pytest.raises(SQLAlchemyError):
        await service.update_status(case_id, finding.id, "under_review")
    monkeypatch.setattr(AsyncSession, "commit", real_commit)

    stored = await service.get_for_case(case_id, finding.id)
    assert stored.status == "candidate"
    assert await _count_finding_review_items(database, case_id, finding.id) == 0


async def test_pc44_historical_under_review_without_item_is_repaired(
    tmp_path: Path,
) -> None:
    """PC4.4: 历史脏状态（Finding=under_review、无 ReviewItem）可被修复。"""
    from sqlalchemy import update as sa_update

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc44.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="历史脏状态结论")

    # 构造补丁前的脏状态：Finding 已 under_review，但 ReviewItem 不存在
    async with database.session_factory() as session:
        await session.execute(
            sa_update(FindingRecord)
            .where(FindingRecord.id == finding.id)
            .values(status="under_review")
        )
        await session.commit()

    updated, item = await repository.submit_finding_for_review(
        case_id=case_id, finding_id=finding.id, summary=finding.statement
    )

    assert updated.status == "under_review"
    assert item.status == "unreviewed"
    assert await _count_finding_review_items(database, case_id, finding.id) == 1


async def test_pc45_verified_finding_resubmit_reuses_review_item(
    tmp_path: Path,
) -> None:
    """PC4.5: verified Finding 重新复审 → 复用同一 item 并激活为 in_review。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc45.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="复核结论")
    approved_item, _ = await _approve_finding_through_review(
        database, repository, service, case_id, finding
    )
    assert (await service.get_for_case(case_id, finding.id)).status == "verified"
    assert approved_item.status == "accepted"

    reopened = await service.update_status(case_id, finding.id, "under_review")

    assert reopened.status == "under_review"
    assert await _count_finding_review_items(database, case_id, finding.id) == 1
    items = await repository.list_review_items(
        case_id, object_type="finding", limit=100
    )
    item = next(i for i in items if i.object_id == finding.id)
    assert item.id == approved_item.id
    assert item.status == "in_review"


async def test_pc46_rejected_finding_resubmit_reuses_review_item(
    tmp_path: Path,
) -> None:
    """PC4.6: rejected Finding 重新复审 → 复用同一 item 并激活为 in_review。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc46.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="被否决后的复核")
    await service.update_status(case_id, finding.id, "under_review")
    item = await _seed_review_item(repository, database, case_id, finding)
    result = await repository.decide_review_item(
        item_id=item.id,
        expected_status="under_review",
        expected_version=item.current_version,
        target_status="rejected",
        decision=ReviewDecisionRecord(
            item_id=item.id,
            object_version=item.current_version,
            decision="rejected",
            reason="证据不足",
            actor="tester",
        ),
    )
    assert result is not None
    assert (await service.get_for_case(case_id, finding.id)).status == "rejected"

    reopened = await service.update_status(case_id, finding.id, "under_review")

    assert reopened.status == "under_review"
    assert await _count_finding_review_items(database, case_id, finding.id) == 1
    items = await repository.list_review_items(
        case_id, object_type="finding", limit=100
    )
    current = next(i for i in items if i.object_id == finding.id)
    assert current.id == item.id
    assert current.status == "in_review"


async def test_pc47_superseded_finding_cannot_resubmit(tmp_path: Path) -> None:
    """PC4.7: superseded Finding 拒绝重新提交，且不创建/激活 ReviewItem。"""
    from sqlalchemy import update as sa_update

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc47.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="已取代结论")
    async with database.session_factory() as session:
        await session.execute(
            sa_update(FindingRecord)
            .where(FindingRecord.id == finding.id)
            .values(status="superseded")
        )
        await session.commit()

    with pytest.raises(ApplicationError) as excinfo:
        await service.update_status(case_id, finding.id, "under_review")
    assert excinfo.value.code == "finding_invalid_transition"
    assert await _count_finding_review_items(database, case_id, finding.id) == 0


async def test_pc48_concurrent_submit_keeps_single_review_item(
    tmp_path: Path,
) -> None:
    """PC4.8: 并发提交两个事务 → 唯一约束兜底，最终只有 1 个 item。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc48.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="并发提交结论")

    results = await asyncio.gather(
        repository.submit_finding_for_review(
            case_id=case_id, finding_id=finding.id, summary=finding.statement
        ),
        repository.submit_finding_for_review(
            case_id=case_id, finding_id=finding.id, summary=finding.statement
        ),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, Exception)]
    assert errors == [], f"并发提交出现异常: {errors}"

    assert (await service.get_for_case(case_id, finding.id)).status == "under_review"
    assert await _count_finding_review_items(database, case_id, finding.id) == 1


async def test_pc2b_workbench_reopen_accepted_finding_syncs_both(
    tmp_path: Path,
) -> None:
    """PC2B Test A: Review Workbench 重开 accepted Finding → item=in_review
    且 Finding=under_review（同一逻辑操作，同一 item）。"""
    from app.application.review_service import ReviewService

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc2b-a.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="工作台重开结论")
    approved_item, _ = await _approve_finding_through_review(
        database, repository, service, case_id, finding
    )
    assert approved_item.status == "accepted"
    assert (await service.get_for_case(case_id, finding.id)).status == "verified"

    review_service = ReviewService(repository)
    reopened = await review_service.reopen(approved_item.id, case_id=case_id)

    assert reopened.id == approved_item.id
    assert reopened.status == "in_review"
    assert (await service.get_for_case(case_id, finding.id)).status == "under_review"


async def test_pc2b_workbench_reopen_rejected_finding_syncs_both(
    tmp_path: Path,
) -> None:
    """PC2B Test B: 重开 rejected Finding → 同样原子同步到 under_review。"""
    from app.application.review_service import ReviewService

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc2b-b.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="工作台重开否决结论")
    await service.update_status(case_id, finding.id, "under_review")
    item = await _seed_review_item(repository, database, case_id, finding)
    result = await repository.decide_review_item(
        item_id=item.id,
        expected_status="under_review",
        expected_version=item.current_version,
        target_status="rejected",
        decision=ReviewDecisionRecord(
            item_id=item.id,
            object_version=item.current_version,
            decision="rejected",
            reason="证据不足",
            actor="tester",
        ),
    )
    assert result is not None
    assert (await service.get_for_case(case_id, finding.id)).status == "rejected"

    review_service = ReviewService(repository)
    reopened = await review_service.reopen(item.id, case_id=case_id)

    assert reopened.id == item.id
    assert reopened.status == "in_review"
    assert (await service.get_for_case(case_id, finding.id)).status == "under_review"


async def test_pc2b_reopen_transaction_failure_no_partial_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PC2B Test C: 重开事务 commit 失败 → ReviewItem 与 Finding 都保持不变。"""
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.application.review_service import ReviewService

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc2b-c.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="重开回滚结论")
    approved_item, _ = await _approve_finding_through_review(
        database, repository, service, case_id, finding
    )
    assert approved_item.status == "accepted"
    assert (await service.get_for_case(case_id, finding.id)).status == "verified"

    real_commit = AsyncSession.commit

    async def failing_commit(self: AsyncSession) -> None:
        raise SQLAlchemyError("simulated reopen write failure")

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    with pytest.raises(SQLAlchemyError):
        await ReviewService(repository).reopen(approved_item.id, case_id=case_id)
    monkeypatch.setattr(AsyncSession, "commit", real_commit)

    stored_item = await repository.get_review_item(approved_item.id)
    assert stored_item.status == "accepted"
    assert (await service.get_for_case(case_id, finding.id)).status == "verified"


async def test_pc2b_reopen_claim_item_keeps_original_behavior(
    tmp_path: Path,
) -> None:
    """PC2B Test D: 非 Finding ReviewItem 重开保持原行为（不访问 Finding）。"""
    from app.application.review_service import ReviewService

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc2b-d.db'}")
    repository, service, case_id = await _seed(database)
    claim_item = await repository.create_review_item(
        ReviewItemRecord(
            case_id=case_id,
            object_type="claim",
            object_id="claim-xyz",
            summary="主张待复核",
            status="accepted",
        )
    )
    assert claim_item.status == "accepted"

    reopened = await ReviewService(repository).reopen(claim_item.id, case_id=case_id)

    assert reopened.id == claim_item.id
    assert reopened.status == "in_review"


async def test_pc22_under_review_to_candidate_keeps_review_item(
    tmp_path: Path,
) -> None:
    """PC2.2: under_review → candidate 保留既有 ReviewItem（本轮不扩展撤回语义）。"""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pc22.db'}")
    repository, service, case_id = await _seed(database)
    finding = await service.create_manual(case_id, statement="撤回审核结论")
    await service.update_status(case_id, finding.id, "under_review")
    assert await _count_finding_review_items(database, case_id, finding.id) == 1

    reverted = await service.update_status(case_id, finding.id, "candidate")

    assert reverted.status == "candidate"
    # 既有 ReviewItem 保持存在（unreviewed），不应被删除或覆盖
    assert await _count_finding_review_items(database, case_id, finding.id) == 1
    items = await repository.list_review_items(
        case_id, object_type="finding", limit=100
    )
    item = next(i for i in items if i.object_id == finding.id)
    assert item.status == "unreviewed"



