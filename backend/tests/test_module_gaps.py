"""Module semantic/concurrency gaps (round 4): M13/M17/M19/M23 fixes.

- M19 HttpOtlpExporter：有界缓冲 + 后台批量上报；导出绝不阻塞业务；
  otlp_http 无端点时回退 noop。
- M23 旧写入入口治理化：_persist_governed_memory 在装配 governance 时经
  Gate 落库，未装配时回退原路径。
- M13 分享下载计数原子自增（防并发丢失更新）。
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

from app.harness.tool_factory import _persist_governed_memory
from app.infrastructure.database.engine import Database
from app.schemas.knowledge import CreateMemoryRequest
from app.telemetry.exporter_factory import EXPORTER_OTLP_HTTP, build_exporter, build_telemetry
from app.telemetry.tracer import (
    HttpOtlpExporter,
    NoopExporter,
    Span,
)

_DB_ROOT = "E:/Graduate_work_folder/Agent_develop/Project/COIFESP_Agent/Project/backend/data"


def _db_url(name: str) -> str:
    return "sqlite+aiosqlite:///" + _DB_ROOT.replace("\\", "/") + "/" + name


def _cleanup_db(name: str) -> None:
    path = os.path.join(_DB_ROOT, name)
    try:
        os.remove(path)
    except OSError:
        pass


def _span(name: str = "test.span") -> Span:
    return Span(
        name=name,
        trace_id="t" * 32,
        span_id="s" * 16,
        attributes={"service.name": "test"},
        status="ok",
    )


# ---------- M19：HttpOtlpExporter（无 DB） ----------

def test_http_otlp_exporter_never_blocks_and_drops_on_full() -> None:
    exporter = HttpOtlpExporter(
        endpoint="http://127.0.0.1:1/v1/traces",  # 不可达端点
        flush_interval_seconds=0.5,
        batch_size=4,
        queue_capacity=8,
    )
    try:
        # 入队不抛错（业务绝不被遥测阻断）。
        for _ in range(20):
            exporter.export(_span())
        stats = exporter.stats()
        assert stats["sent"] + stats["dropped"] <= 20
        # 队列有界：超过容量时丢弃而非阻塞。
        assert stats["queued"] <= 8
    finally:
        exporter._stopped = True


def test_build_exporter_otlp_without_endpoint_falls_back_noop() -> None:
    exporter = build_exporter(EXPORTER_OTLP_HTTP, otlp_endpoint=None)
    assert isinstance(exporter, NoopExporter)
    exporter2 = build_exporter(EXPORTER_OTLP_HTTP, otlp_endpoint="http://collector:4318/v1/traces")
    assert isinstance(exporter2, HttpOtlpExporter)
    exporter2._stopped = True


def test_build_telemetry_otlp_wiring() -> None:
    telemetry = build_telemetry(
        exporter_kind=EXPORTER_OTLP_HTTP,
        otlp_endpoint="http://collector:4318/v1/traces",
        otlp_service_name="coifesp-test",
    )
    assert isinstance(telemetry.exporter, HttpOtlpExporter)
    telemetry.exporter._stopped = True


# ---------- M23：治理化写入辅助（无 DB） ----------

def test_persist_governed_memory_fallback_without_governance() -> None:
    """未装配 governance 时回退 knowledge.create_memory（兼容旧构造）。"""
    calls: dict[str, object] = {}

    class FakeKnowledge:
        async def create_memory(self, case_id, request, embedding=None):
            calls["case_id"] = case_id
            calls["embedding"] = embedding
            return "created"

    async def run() -> None:
        result = await _persist_governed_memory(
            None,
            FakeKnowledge(),
            "case-1",
            CreateMemoryRequest(
                scope="case",
                kind="fact",
                content="x",
                source_type="test",
                source_id="1",
            ),
            memory_type="case_fact",
            trust_level="tool_diagnostic",
            embedding=[0.1],
        )
        assert result == "created"
        assert calls["case_id"] == "case-1"
        assert calls["embedding"] == [0.1]

    asyncio.run(run())


def test_persist_governed_memory_uses_gate_when_assembled() -> None:
    """装配 governance 时经 Gate 落库（外部内容不得自行提升信任）。"""
    seen: dict[str, object] = {}

    class FakeGovernance:
        async def persist_governed(self, **kwargs):
            seen.update(kwargs)
            return "governed"

    class FakeKnowledge:
        async def create_memory(self, *args, **kwargs):
            raise AssertionError("should not bypass governance")

    async def run() -> None:
        result = await _persist_governed_memory(
            FakeGovernance(),
            FakeKnowledge(),
            "case-1",
            CreateMemoryRequest(
                scope="case",
                kind="fact",
                content="外部帖子内容",
                source_type="social_post",
                source_id="p1",
            ),
            memory_type="case_hypothesis",
            trust_level="external_content",
            has_evidence=False,
        )
        assert result == "governed"
        assert seen["memory_type"] == "case_hypothesis"
        assert seen["trust_level"] == "external_content"
        assert seen["case_id"] == "case-1"

    asyncio.run(run())


# ---------- M13：分享下载计数原子自增（集成，单次建库） ----------

def test_share_download_atomic_bump() -> None:
    _cleanup_db("module_gaps.db")
    database = Database(_db_url("module_gaps.db"))

    async def run() -> None:
        await database.create_schema()
        from app.application.repositories import ApplicationRepository
        from app.infrastructure.database.models import ShareLinkRecord
        from app.services.notifications import hash_token

        repo = ApplicationRepository(database)
        from app.schemas.cases import CreateCaseRequest

        case = await repo.create_case(
            CreateCaseRequest(title="share test", topic="tt", platforms=["weibo"])
        )
        link = ShareLinkRecord(
            case_id=case.id,
            target_type="report",
            target_id="art-1",
            token_hash=hash_token("tok-1"),
            download_limit=5,
            download_count=0,
        )
        created = await repo.create_share_link(link)
        # 原子消费总配额和分钟窗口，不丢失更新。
        now = datetime.now(UTC)
        assert await repo.consume_share_download(created.id, per_minute=60, now=now)
        assert await repo.consume_share_download(created.id, per_minute=60, now=now)
        record = await repo.get_share_link_by_hash(hash_token("tok-1"))
        assert record is not None
        assert record.download_count == 2

    async def _main() -> None:
        await run()
        await database.dispose()

    asyncio.run(_main())
    _cleanup_db("module_gaps.db")


# ---------- M10：叙事合并撤销（API，单次建库） ----------

def test_narrative_merge_undo_restores_source() -> None:
    _cleanup_db("module_gaps_narrative.db")
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    app = create_app(Settings(database_url=_db_url("module_gaps_narrative.db"), demo_mode=True))
    with TestClient(app) as client:
        case = client.post(
            "/api/v1/cases",
            json={"title": "叙事撤销", "topic": "新能源", "platforms": ["weibo"]},
        )
        case_id = case.json()["id"]

        from app.infrastructure.database.models import (
            NarrativeClaimRecord,
            NarrativeRecord,
        )

        async def seed() -> str:
            container = client.app.state.container
            run = await container.repository.create_agent_run(
                case_id=case_id, turn_id=None, objective="seed claim"
            )
            claim = await container.repository.create_claim(
                case_id=case_id,
                text="主张A",
                created_by_run_id=run.id,
            )
            return claim.id

        claim_id = client.portal.call(seed)

        async def seed_narratives() -> dict[str, str]:
            container = client.app.state.container
            src = await container.repository.create_narrative(
                NarrativeRecord(
                    case_id=case_id, title="源叙事", canonical_summary="", created_source="auto"
                )
            )
            tgt = await container.repository.create_narrative(
                NarrativeRecord(
                    case_id=case_id, title="目标叙事", canonical_summary="", created_source="auto"
                )
            )
            await container.repository.add_narrative_claim(
                NarrativeClaimRecord(
                    narrative_id=src.id, claim_id=claim_id, membership_score=1.0
                )
            )
            return {"src": src.id, "tgt": tgt.id}

        ids = client.portal.call(seed_narratives)
        # 合并
        merged = client.post(
            f"/api/v1/cases/{case_id}/narratives/{ids['src']}:merge",
            json={"target_narrative_id": ids["tgt"]},
        )
        assert merged.status_code == 200
        assert merged.json()["archived"] == ids["src"]
        # 目标包含合并成员
        target_members = client.portal.call(
            lambda: client.app.state.container.repository.list_narrative_members(ids["tgt"])
        )
        assert claim_id in target_members["claims"]
        # 撤销合并
        undone = client.post(
            f"/api/v1/cases/{case_id}/narratives/{ids['src']}:undo-merge",
        )
        assert undone.status_code == 200
        assert undone.json()["restored"] == ids["src"]
        assert undone.json()["removed_members"] >= 1
        # 来源恢复 active；目标成员已移除（仅 human_merge 来源）
        src = client.portal.call(
            lambda: client.app.state.container.repository.get_narrative(ids["src"])
        )
        assert src.status == "active"
        target_members2 = client.portal.call(
            lambda: client.app.state.container.repository.list_narrative_members(ids["tgt"])
        )
        assert claim_id not in target_members2["claims"]


# ---------- M11：span_map 可靠性（无 DB） ----------

def test_normalizer_span_mapping_after_url_placeholder() -> None:
    """URL 占位后，后续文本的 span 能精确映射回原文（不因长度变化错位）。"""
    from app.services.semantics import TextNormalizer

    original = "看 https://x.com/abcdefghij 链接"
    url_start = original.index("https")
    url_end = url_start + len("https://x.com/abcdefghij")
    normalized = TextNormalizer().normalize(original)
    assert "《URL》" in normalized.text
    # URL 占位段的 span：覆盖整个 URL 原文区间（占位段起点在《URL》之前）。
    url_norm_start = normalized.text.index("《URL》")
    url_span = normalized.orig_span(url_norm_start, url_norm_start + len("《URL》"))
    assert url_span == (url_start, url_end)
    # URL 之后文本（"链接"）的 span 映射回原文正确位置。
    link_norm = normalized.text.index("链接")
    link_span = normalized.orig_span(link_norm, link_norm + 2)
    assert original[link_span[0]:link_span[1]] == "链接"
    assert link_span[0] == original.index("链接")


def test_normalizer_span_mapping_after_repeat_compression() -> None:
    """重复字符压缩后，段内任意子区间映射回完整的原始重复区间。"""
    from app.services.semantics import TextNormalizer

    original = "啊啊啊啊啊好"
    normalized = TextNormalizer().normalize(original)
    assert normalized.text == "啊好"
    # 归一化第 0 个字符"啊"覆盖原文 0..5（5 个重复字符）。
    span = normalized.orig_span(0, 1)
    assert span == (0, 5)
    assert original[span[0]:span[1]] == "啊啊啊啊啊"
    # "好"的 span 不受影响。
    good_span = normalized.orig_span(1, 2)
    assert original[good_span[0]:good_span[1]] == "好"


def test_normalizer_span_mapping_fullwidth_folding() -> None:
    """全角折叠（NFKC）后 span 映射回原始全角字符位置。"""
    from app.services.semantics import TextNormalizer

    original = "ＡＢＣ测试"
    normalized = TextNormalizer().normalize(original)
    assert normalized.text == "ABC测试"
    # 归一化"A"对应原文全角Ａ（索引 0）。
    span = normalized.orig_span(0, 1)
    assert span == (0, 1)
    assert original[span[0]:span[1]] == "Ａ"
    # 混合占位 + 折叠：URL 后紧跟全角字符。
    mixed = "看https://a.co/xxＢ"
    nm = TextNormalizer().normalize(mixed)
    assert "《URL》" in nm.text
    last_span = nm.orig_span(len(nm.text) - 1, len(nm.text))
    assert mixed[last_span[0]:last_span[1]] == "Ｂ"


# ---------- M10：叙事拆分撤销（API） ----------

def test_narrative_split_undo_archives_empty_split() -> None:
    """undo-split：拆分出的空叙事归档恢复；有成员时拒绝防静默丢失。"""
    _cleanup_db("module_gaps_split.db")
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    app = create_app(
        Settings(database_url=_db_url("module_gaps_split.db"), demo_mode=True)
    )
    with TestClient(app) as client:
        case = client.post(
            "/api/v1/cases",
            json={"title": "拆分撤销", "topic": "新能源", "platforms": ["weibo"]},
        )
        case_id = case.json()["id"]

        from app.infrastructure.database.models import (
            NarrativeClaimRecord,
            NarrativeRecord,
        )

        async def seed() -> str:
            container = client.app.state.container
            run = await container.repository.create_agent_run(
                case_id=case_id, turn_id=None, objective="seed split"
            )
            claim = await container.repository.create_claim(
                case_id=case_id, text="拆分主张", created_by_run_id=run.id
            )
            return claim.id

        claim_id = client.portal.call(seed)

        async def seed_source() -> str:
            container = client.app.state.container
            src = await container.repository.create_narrative(
                NarrativeRecord(
                    case_id=case_id, title="源叙事", canonical_summary="", created_source="auto"
                )
            )
            await container.repository.add_narrative_claim(
                NarrativeClaimRecord(
                    narrative_id=src.id, claim_id=claim_id, membership_score=1.0
                )
            )
            return src.id

        src_id = client.portal.call(seed_source)

        # 拆分（创建空叙事）
        split = client.post(
            f"/api/v1/cases/{case_id}/narratives/{src_id}:split",
            json={"title": "拆分目标"},
        )
        assert split.status_code == 200
        split_id = split.json()["narrative_id"]

        # 撤销拆分：空叙事归档恢复
        undone = client.post(
            f"/api/v1/cases/{case_id}/narratives/{src_id}:undo-split",
        )
        assert undone.status_code == 200, undone.text
        assert undone.json()["archived_split"] == split_id
        archived = client.portal.call(
            lambda: client.app.state.container.repository.get_narrative(split_id)
        )
        assert archived.status == "archived"

        # 无 human_split transition 时撤销被拒
        again = client.post(
            f"/api/v1/cases/{case_id}/narratives/{src_id}:undo-split",
        )
        assert again.status_code == 422

        # 拆分叙事已有成员时拒绝（防静默数据丢失）
        split2 = client.post(
            f"/api/v1/cases/{case_id}/narratives/{src_id}:split",
            json={"title": "拆分目标2"},
        )
        split2_id = split2.json()["narrative_id"]
        async def assign_member() -> None:
            container = client.app.state.container
            await container.repository.add_narrative_claim(
                NarrativeClaimRecord(
                    narrative_id=split2_id, claim_id=claim_id, membership_score=1.0
                )
            )
        client.portal.call(assign_member)
        denied = client.post(
            f"/api/v1/cases/{case_id}/narratives/{src_id}:undo-split",
        )
        assert denied.status_code == 422
    _cleanup_db("module_gaps_split.db")