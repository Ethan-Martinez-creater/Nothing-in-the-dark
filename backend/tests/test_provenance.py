"""C5: Provenance correctness 与双向链路测试。

- 真实 Artifact 无 Finding 仍可查（resolver 直读 ArtifactRecord）
- Artifact → Finding / Evidence → Finding downstream
- Finding upstream 兼容历史脏 link（dangling_evidence_ref warning，不伪造 node）
- Finding → ReportDocument（cited_by）与 ReportDocument → citations
- ReportDocument revision 链（superseded_by）
- 跨 case root 统一 404（provenance_object_not_found）
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.provenance_service import ProvenanceService
from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.infrastructure.database import Database
from app.main import create_app
from app.schemas.cases import CreateCaseRequest

from app.infrastructure.database.models import (
    ArtifactRecord,
    EvidenceRecord,
    FindingEvidenceLinkRecord,
    FindingRecord,
    FindingSourceLinkRecord,
    ReportDocumentRecord,
)


async def _seed_provenance_graph(
    database: Database,
) -> tuple[ProvenanceService, str, dict[str, str]]:
    """构建完整链路：Evidence → Finding ← Artifact；Report(r1 ← r2 revision)。"""
    await database.create_schema()
    repository = ApplicationRepository(database)
    service = ProvenanceService(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="溯源案例", platforms=["weibo"])
    )
    other = await repository.create_case(
        CreateCaseRequest(topic="其他案例", platforms=["weibo"])
    )
    case_id = case.id
    async with database.session_factory() as session:
        # 真实 evidence + 一条历史脏 link（ev-dangling 不存在）
        session.add(
            EvidenceRecord(
                id="ev-p1", case_id=case_id, source_type="social_post",
                source_id="post-1", excerpt="关键摘录",
            )
        )
        finding = FindingRecord(
            id="f-prov", case_id=case_id, kind="opinion",
            title="协同传播", statement="多平台存在协同痕迹", status="candidate",
        )
        session.add(finding)
        session.add(
            FindingEvidenceLinkRecord(
                finding_id="f-prov", evidence_ref="ev-p1", relation="supports"
            )
        )
        # 历史脏数据：C2 前可能写入的幽灵 evidence link
        session.add(
            FindingEvidenceLinkRecord(
                finding_id="f-prov", evidence_ref="ev-dangling", relation="supports"
            )
        )
        # artifact root + source link
        session.add(
            ArtifactRecord(
                id="art-prov", case_id=case_id, run_id=None, kind="opinion_analysis",
                title="观点分析", data={},
            )
        )
        session.add(
            FindingSourceLinkRecord(
                finding_id="f-prov", source_type="artifact",
                source_id="art-prov", source_path="conclusions[0]",
            )
        )
        # 报告 r1 引用 ev-p1 + f-prov；r2 是 r1 的 revision
        session.add(
            ReportDocumentRecord(
                id="r-prov-1", family_id="fam-1", case_id=case_id,
                source_artifact_id="art-prov", status="published",
                title="报告一",
                content_json={
                    "citation_links": [
                        {"conclusion": "c1", "evidence_ids": ["ev-p1"]},
                        {"finding_id": "f-prov"},
                    ]
                },
                lock_version=1,
            )
        )
        session.add(
            ReportDocumentRecord(
                id="r-prov-2", family_id="fam-1", case_id=case_id,
                source_artifact_id="art-prov", supersedes_id="r-prov-1",
                status="draft", title="报告一修订", content_json={}, lock_version=1,
            )
        )
        await session.commit()
    del other
    return service, case_id, {"finding": "f-prov", "artifact": "art-prov", "evidence": "ev-p1"}


async def test_real_artifact_without_finding_resolvable(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pv1.db'}")
    service, case_id, _ = await _seed_provenance_graph(database)
    repository = ApplicationRepository(database)
    orphan = await repository.create_artifact(
        case_id=case_id, run_id=None, kind="report", title="未物化报告", data={}
    )
    result = await service.one_hop(case_id, "artifact", orphan.id)
    assert result["root"] == {"type": "artifact", "id": orphan.id}
    assert result["upstream"] == []
    assert result["downstream"] == []


async def test_artifact_and_evidence_downstream_finding(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pv2.db'}")
    service, case_id, ids = await _seed_provenance_graph(database)

    art = await service.one_hop(case_id, "artifact", ids["artifact"])
    assert [(d["type"], d["id"], d["relation"]) for d in art["downstream"]] == [
        ("finding", "f-prov", "materialized_to")
    ]

    ev = await service.one_hop(case_id, "evidence", ids["evidence"])
    assert [(d["type"], d["id"], d["relation"]) for d in ev["downstream"]] == [
        ("finding", "f-prov", "supports")
    ]


async def test_finding_upstream_dangling_warning_and_report_downstream(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pv3.db'}")
    service, case_id, ids = await _seed_provenance_graph(database)

    result = await service.one_hop(case_id, "finding", ids["finding"])
    # upstream：真实 evidence + artifact 来源；幽灵 evidence 不输出为 node
    upstream_pairs = {(u["type"], u["id"]) for u in result["upstream"]}
    assert ("evidence", "ev-p1") in upstream_pairs
    assert ("artifact", "art-prov") in upstream_pairs
    assert ("evidence", "ev-dangling") not in upstream_pairs
    # 幽灵 link 以 warning 输出
    assert {"type": "dangling_evidence_ref", "evidence_ref": "ev-dangling",
            "relation": "supports"} in result["warnings"]
    # downstream：报告 r1 引用了该 finding
    assert [(d["type"], d["id"], d["relation"]) for d in result["downstream"]] == [
        ("report_document", "r-prov-1", "cited_by")
    ]


async def test_generic_finding_citation_is_bidirectional(tmp_path: Path) -> None:
    """FC4: {"ref": finding_id} generic citation 双向一致。

    Report provenance 把 generic ref 解析为 finding（upstream），
    Finding provenance 的 downstream 通过 generic 解析命中同一报告。
    """
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pv6.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    service = ProvenanceService(database)
    case = await repository.create_case(
        CreateCaseRequest(topic="通用引用", platforms=["weibo"])
    )
    async with database.session_factory() as session:
        session.add(
            FindingRecord(
                id="f-gen", case_id=case.id, kind="opinion",
                title="通用引用结论", statement="多平台转发节奏一致",
                status="candidate",
            )
        )
        session.add(
            ArtifactRecord(
                id="art-gen", case_id=case.id, run_id=None,
                kind="opinion_analysis", title="报告来源", data={},
            )
        )
        session.add(
            ReportDocumentRecord(
                id="r-gen", family_id="fam-gen", case_id=case.id,
                source_artifact_id="art-gen", status="draft",
                title="通用引用报告",
                content_json={
                    "citation_links": [{"conclusion": "c1", "ref": "f-gen"}]
                },
                lock_version=1,
            )
        )
        await session.commit()

    # Report → upstream：generic ref 解析为 finding
    report_view = await service.one_hop(case.id, "report_document", "r-gen")
    assert ("finding", "f-gen") in {
        (u["type"], u["id"]) for u in report_view["upstream"]
    }

    # Finding → downstream：generic citation 命中同一 report document
    finding_view = await service.one_hop(case.id, "finding", "f-gen")
    assert ("report_document", "r-gen", "cited_by") in [
        (d["type"], d["id"], d["relation"]) for d in finding_view["downstream"]
    ]


async def test_report_document_refs_and_revision_chain(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pv4.db'}")
    service, case_id, _ = await _seed_provenance_graph(database)

    r1 = await service.one_hop(case_id, "report_document", "r-prov-1")
    upstream_pairs = {(u["type"], u["id"], u["relation"]) for u in r1["upstream"]}
    assert upstream_pairs == {
        ("evidence", "ev-p1", "cited"),
        ("finding", "f-prov", "cited"),
    }
    assert [(d["type"], d["id"], d["relation"]) for d in r1["downstream"]] == [
        ("report_document", "r-prov-2", "superseded_by")
    ]

    # 修订版没有后继，也没有 citation upstream
    r2 = await service.one_hop(case_id, "report_document", "r-prov-2")
    assert r2["upstream"] == []
    assert r2["downstream"] == []


async def test_provenance_cross_case_root_unified_404(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pv5.db'}")
    service, case_id, ids = await _seed_provenance_graph(database)
    repository = ApplicationRepository(database)
    other = await repository.create_case(
        CreateCaseRequest(topic="第三案例", platforms=["weibo"])
    )

    for object_type, object_id in (
        ("finding", ids["finding"]),
        ("artifact", ids["artifact"]),
        ("report_document", "r-prov-1"),
    ):
        with pytest.raises(ApplicationError) as exc:
            await service.one_hop(other.id, object_type, object_id)
        assert exc.value.code == "provenance_object_not_found"


def test_provenance_api_report_document_root(tmp_path: Path) -> None:
    """API 层冒烟：artifact / report_document root 可查询，unknown type 400。"""
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'pv6.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case_id = client.post(
            "/api/v1/cases", json={"topic": "API 溯源", "platforms": ["weibo"]}
        ).json()["id"]
        container = app.state.container
        import asyncio

        async def seed() -> None:
            async with container.database.session_factory() as session:
                session.add(
                    ReportDocumentRecord(
                        id="r-api-1", family_id="fam-api", case_id=case_id,
                        source_artifact_id="art-seed", status="draft",
                        title="API 报告",
                        content_json={"citation_links": []},
                        lock_version=1,
                    )
                )
                session.add(
                    ArtifactRecord(
                        id="art-seed", case_id=case_id, run_id=None,
                        kind="report", title="来源 artifact", data={},
                    )
                )
                await session.commit()

        asyncio.run(seed())

        found = client.get(f"/api/v1/cases/{case_id}/provenance/report_document/r-api-1")
        assert found.status_code == 200
        root = found.json()["root"]
        assert root["type"] == "report_document"
        assert root["id"] == "r-api-1"

        missing = client.get(
            f"/api/v1/cases/{case_id}/provenance/report_document/no-such"
        )
        assert missing.status_code == 404
        assert missing.json()["code"] == "provenance_object_not_found"
