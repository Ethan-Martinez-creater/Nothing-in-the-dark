"""M23: memory safety & governance - write gate, lifecycle, conflicts, API."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.memory_governance import MemoryGovernanceService
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.infrastructure.database.engine import Database
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.main import create_app
from app.schemas.knowledge import CreateMemoryRequest
from app.services.content_security import (
    TRUST_EXTERNAL_CONTENT,
    TRUST_GENERATED_CONTENT,
    TRUST_OPERATOR_INPUT,
    TRUST_REVIEWED_EVIDENCE,
    TRUST_SYSTEM_CONTROL,
    ContentSecurityService,
)
from app.services.memory_governance import (
    MEMORY_STATUS_ACTIVE,
    MEMORY_STATUS_DELETED,
    MEMORY_STATUS_DISABLED,
    MEMORY_STATUS_PENDING_REVIEW,
    MEMORY_STATUS_SUPERSEDED,
    MEMORY_TYPE_CASE_FACT,
    MEMORY_TYPE_CASE_HYPOTHESIS,
    MEMORY_TYPE_CONVERSATION_SUMMARY,
    MEMORY_TYPE_EXTERNAL_EXCERPT,
    MEMORY_TYPE_OPERATOR_PREFERENCE,
    MEMORY_TYPE_PROCEDURAL,
    MemoryWriteGate,
    detect_conflict,
    memory_type_for_kind,
    scan_for_secrets,
    sensitivity_of,
    status_transition,
    summary_tag,
)

_DB_ROOT = "E:/Graduate_work_folder/Agent_develop/Project/COIFESP_Agent/Project/backend/data"

# ---------- 写入 Gate 单元 ----------

def test_gate_rejects_injection_to_high_trust_memory() -> None:
    # 外部帖子含指令覆盖信号 -> 高风险 -> deny（M16 兜底）。
    decision = MemoryWriteGate.evaluate(
        memory_type=MEMORY_TYPE_CASE_FACT,
        trust_level=TRUST_EXTERNAL_CONTENT,
        risk_score=1.0,
        has_evidence=False,
    )
    assert decision.decision == "deny"


def test_gate_external_content_fact_needs_review() -> None:
    decision = MemoryWriteGate.evaluate(
        memory_type=MEMORY_TYPE_CASE_FACT,
        trust_level=TRUST_EXTERNAL_CONTENT,
        risk_score=0.1,
    )
    assert decision.decision == "needs_review"
    assert decision.review_state == "pending_review"


def test_gate_secret_content_denied() -> None:
    has_secret, hints = scan_for_secrets("password=super-secret-abc123")
    assert has_secret is True
    assert hints
    assert sensitivity_of("password=super-secret-abc123") == "high"


def test_gate_procedural_requires_system_control() -> None:
    # 越权 procedural 写入必须拒绝（外部/推断来源成功率 0）。
    decision = MemoryWriteGate.evaluate(
        memory_type=MEMORY_TYPE_PROCEDURAL,
        trust_level=TRUST_EXTERNAL_CONTENT,
        risk_score=0.0,
    )
    assert decision.decision == "deny"
    allowed = MemoryWriteGate.evaluate(
        memory_type=MEMORY_TYPE_PROCEDURAL,
        trust_level=TRUST_SYSTEM_CONTROL,
        risk_score=0.0,
    )
    assert allowed.decision == "allow"


def test_gate_hypothesis_allowed_never_fact() -> None:
    decision = MemoryWriteGate.evaluate(
        memory_type=MEMORY_TYPE_CASE_HYPOTHESIS,
        trust_level=TRUST_GENERATED_CONTENT,
        risk_score=0.0,
    )
    assert decision.decision == "allow"
    assert memory_type_for_kind("platform_profile") == MEMORY_TYPE_CASE_HYPOTHESIS


def test_gate_fact_requires_evidence() -> None:
    without_evidence = MemoryWriteGate.evaluate(
        memory_type=MEMORY_TYPE_CASE_FACT,
        trust_level=TRUST_REVIEWED_EVIDENCE,
        risk_score=0.0,
        has_evidence=False,
    )
    assert without_evidence.decision == "needs_review"
    with_evidence = MemoryWriteGate.evaluate(
        memory_type=MEMORY_TYPE_CASE_FACT,
        trust_level=TRUST_REVIEWED_EVIDENCE,
        risk_score=0.0,
        has_evidence=True,
    )
    assert with_evidence.decision == "allow"
    assert with_evidence.review_state == "accepted"


def test_gate_preference_requires_operator_explicit() -> None:
    denied = MemoryWriteGate.evaluate(
        memory_type=MEMORY_TYPE_OPERATOR_PREFERENCE,
        trust_level=TRUST_GENERATED_CONTENT,
        risk_score=0.0,
    )
    assert denied.decision == "deny"
    allowed = MemoryWriteGate.evaluate(
        memory_type=MEMORY_TYPE_OPERATOR_PREFERENCE,
        trust_level=TRUST_OPERATOR_INPUT,
        risk_score=0.0,
        explicit_user_input=True,
    )
    assert allowed.decision == "allow"


def test_gate_external_excerpt_not_promoted() -> None:
    decision = MemoryWriteGate.evaluate(
        memory_type=MEMORY_TYPE_EXTERNAL_EXCERPT,
        trust_level=TRUST_EXTERNAL_CONTENT,
        risk_score=0.0,
    )
    assert decision.decision == "deny"


# ---------- 冲突与状态机 ----------

def test_conflict_detection_finds_contradicting_facts() -> None:
    existing = [
        {
            "id": "m1",
            "content": "事件发生地是上海。",
            "memory_type": MEMORY_TYPE_CASE_FACT,
            "status": MEMORY_STATUS_ACTIVE,
        }
    ]
    conflicts = detect_conflict(
        "事件发生地是杭州，不是上海。",
        existing,
        memory_type=MEMORY_TYPE_CASE_FACT,
    )
    assert len(conflicts) == 1
    assert conflicts[0]["memory_id"] == "m1"
    # 相同内容不算冲突
    assert not detect_conflict(
        "事件发生地是上海。", existing, memory_type=MEMORY_TYPE_CASE_FACT
    )


def test_status_transition_machine() -> None:
    assert status_transition(MEMORY_STATUS_ACTIVE, "disable") == MEMORY_STATUS_DISABLED
    assert status_transition(MEMORY_STATUS_DISABLED, "restore") == MEMORY_STATUS_ACTIVE
    assert status_transition(MEMORY_STATUS_ACTIVE, "delete") == MEMORY_STATUS_DELETED
    assert (
        status_transition(MEMORY_STATUS_PENDING_REVIEW, "review_accept")
        == MEMORY_STATUS_ACTIVE
    )
    assert (
        status_transition(MEMORY_STATUS_PENDING_REVIEW, "review_reject")
        == MEMORY_STATUS_DISABLED
    )
    assert (
        status_transition(MEMORY_STATUS_ACTIVE, "correct") == MEMORY_STATUS_SUPERSEDED
    )
    # 非法转移
    assert status_transition(MEMORY_STATUS_DELETED, "restore") is None
    assert status_transition(MEMORY_STATUS_ACTIVE, "review_accept") is None
    assert status_transition(MEMORY_STATUS_SUPERSEDED, "disable") is None


def test_summary_not_fact_tag() -> None:
    assert "摘要为模型生成" in summary_tag(MEMORY_TYPE_CONVERSATION_SUMMARY)
    assert summary_tag(MEMORY_TYPE_CASE_FACT) == ""


# ---------- 集成：Gate 持久化 ----------

def _db_url(name: str) -> str:
    return "sqlite+aiosqlite:///" + _DB_ROOT.replace("\\", "/") + "/" + name


def _cleanup_db(name: str) -> None:
    path = os.path.join(_DB_ROOT, name)
    try:
        os.remove(path)
    except OSError:
        pass



def _make_service(database: Database) -> MemoryGovernanceService:
    return MemoryGovernanceService(
        KnowledgeRepository(database),
        security=ContentSecurityService(mode="enforce"),
        telemetry=None,
    )


class FakeEmbedder:
    """确定性假向量：每个内容返回 [index+1]*dimension。"""

    def __init__(self, dimension: int = 4) -> None:
        self.calls = 0
        self.dimension = dimension

    async def embed(self, contents: list[str]) -> list[list[float]]:
        self.calls += 1
        return [
            [float(index + 1)] * self.dimension
            for index in range(len(contents))
        ]


# ---------- 集成：Gate 持久化（单次建库） ----------

def test_write_gate_persistence_integration() -> None:
    """一个 database 覆盖：外部事实待审核 / 秘密拒绝 / 偏好可写 / 冲突记录。"""
    _cleanup_db("mem_gov_gate.db")
    database = Database(_db_url("mem_gov_gate.db"))

    async def run() -> None:
        await database.create_schema()
        knowledge = KnowledgeRepository(database)
        service = _make_service(database)
        # 外部内容 -> case_fact -> pending_review（不进普通检索）
        external = await service.persist_governed(
            case_id=None,
            request=CreateMemoryRequest(
                scope="case",
                kind="fact",
                content="该账号疑似参与协同转发。",
                source_type="social_post",
                source_id="post-1",
            ),
            memory_type=MEMORY_TYPE_CASE_FACT,
            trust_level=TRUST_EXTERNAL_CONTENT,
        )
        assert external.status == MEMORY_STATUS_PENDING_REVIEW
        active = await knowledge.list_memories(None, status=MEMORY_STATUS_ACTIVE)
        assert all(record.id != external.id for record in active)
        # 秘密内容 -> deny
        with pytest.raises(ApplicationError) as exc_info:
            await service.persist_governed(
                case_id=None,
                request=CreateMemoryRequest(
                    scope="case",
                    kind="preference",
                    content="我的 API 密钥是 sk-abcdefghijklmnopqrstuvwxyz",
                    source_type="user",
                    source_id="turn-1",
                ),
                memory_type=MEMORY_TYPE_OPERATOR_PREFERENCE,
                trust_level=TRUST_OPERATOR_INPUT,
                explicit_user_input=True,
            )
        assert exc_info.value.code == "memory_write_denied"
        # 用户明确偏好 -> active
        pref = await service.persist_governed(
            case_id=None,
            request=CreateMemoryRequest(
                scope="case",
                kind="preference",
                content="后续报告优先使用表格形式。",
                source_type="user",
                source_id="turn-2",
            ),
            memory_type=MEMORY_TYPE_OPERATOR_PREFERENCE,
            trust_level=TRUST_OPERATOR_INPUT,
            explicit_user_input=True,
            has_evidence=True,
        )
        assert pref.status == MEMORY_STATUS_ACTIVE
        # 矛盾事实 -> 生成冲突记录而非静默覆盖
        first = await service.persist_governed(
            case_id=None,
            request=CreateMemoryRequest(
                scope="case",
                kind="fact",
                content="涉事账号源头是 A。",
                source_type="user_correction",
                source_id="turn-1",
            ),
            memory_type=MEMORY_TYPE_CASE_FACT,
            trust_level=TRUST_OPERATOR_INPUT,
            explicit_user_input=True,
            has_evidence=True,
        )
        assert first.status == MEMORY_STATUS_ACTIVE
        second = await service.persist_governed(
            case_id=None,
            request=CreateMemoryRequest(
                scope="case",
                kind="fact",
                content="不对，涉事账号源头应该是 B。",
                source_type="user_correction",
                source_id="turn-2",
            ),
            memory_type=MEMORY_TYPE_CASE_FACT,
            trust_level=TRUST_OPERATOR_INPUT,
            explicit_user_input=True,
            has_evidence=True,
        )
        assert second.status == MEMORY_STATUS_PENDING_REVIEW
        conflicts = await knowledge.list_conflicts(second.id)
        assert len(conflicts) == 1
        assert conflicts[0].conflicting_memory_id == first.id

    async def _main() -> None:
        await run()
        await database.dispose()

    asyncio.run(_main())
    _cleanup_db("mem_gov_gate.db")


def test_lifecycle_persistence_integration() -> None:
    """一个 database 覆盖：disable/restore/delete、correct 版本链、review、
    reindex 幂等与过期维护。"""
    _cleanup_db("mem_gov_lifecycle.db")
    database = Database(_db_url("mem_gov_lifecycle.db"))

    async def run() -> None:
        await database.create_schema()
        knowledge = KnowledgeRepository(database)
        service = _make_service(database)
        embedder = FakeEmbedder()

        async def _pref(content: str, source_id: str) -> Any:
            return await service.persist_governed(
                case_id=None,
                request=CreateMemoryRequest(
                    scope="case",
                    kind="preference",
                    content=content,
                    source_type="user",
                    source_id=source_id,
                ),
                memory_type=MEMORY_TYPE_OPERATOR_PREFERENCE,
                trust_level=TRUST_OPERATOR_INPUT,
                explicit_user_input=True,
                has_evidence=True,
            )

        # ---- 用户控制状态机 ----
        pref = await _pref("偏好：简洁风格。", "turn-1")
        memory_id = pref.id
        disabled = await service.disable_memory(memory_id, actor="user")
        assert disabled.status == MEMORY_STATUS_DISABLED and disabled.active is False
        with pytest.raises(ApplicationError) as exc_info:
            await service.disable_memory(memory_id, actor="user")
        assert exc_info.value.code == "memory_status_conflict"
        restored = await service.restore_memory(memory_id, actor="user")
        assert restored.status == MEMORY_STATUS_ACTIVE
        deleted = await service.delete_memory(memory_id, actor="user")
        assert deleted.status == MEMORY_STATUS_DELETED
        deleted_again = await service.delete_memory(memory_id, actor="user")
        assert deleted_again.status == MEMORY_STATUS_DELETED
        mutations = await knowledge.list_mutations(memory_id)
        # 最新在前（desc）
        assert [entry.action for entry in mutations] == [
            "delete",
            "restore",
            "disable",
        ]
        # ---- 修正 -> 新版本，旧版本不再检索 ----
        old = await _pref("结论：A 平台占比最高。", "turn-3")
        new = await service.correct_memory(
            old.id,
            CreateMemoryRequest(
                scope="case",
                kind="fact",
                content="更正：B 平台占比最高。",
                source_type="user_correction",
                source_id="turn-4",
            ),
            actor="user",
            reason="user correction",
        )
        assert new is not None and new.version == 2 and new.supersedes_id == old.id
        assert new.scope == old.scope and new.kind == old.kind
        active = await knowledge.list_memories(None, status=MEMORY_STATUS_ACTIVE)
        assert all(record.id != old.id for record in active)
        # ---- review：accept -> active；reject -> disabled ----
        pending = await service.persist_governed(
            case_id=None,
            request=CreateMemoryRequest(
                scope="case",
                kind="fact",
                content="外部帖声称事件与平台无关。",
                source_type="social_post",
                source_id="post-1",
            ),
            memory_type=MEMORY_TYPE_CASE_FACT,
            trust_level=TRUST_EXTERNAL_CONTENT,
        )
        assert pending.status == MEMORY_STATUS_PENDING_REVIEW
        accepted = await service.review_memory(
            pending.id, accept=True, actor="reviewer"
        )
        assert accepted is not None and accepted.status == MEMORY_STATUS_ACTIVE
        assert (
            accepted.review_state == "accepted"
            and accepted.last_verified_at is not None
        )
        with pytest.raises(ApplicationError):
            await service.review_memory(pending.id, accept=True, actor="reviewer")
        # ---- reindex：dry-run 计划 -> 幂等执行 ----
        plan = await service.reindex(dry_run=True, embedder=embedder.embed)
        assert plan["dry_run"] is True and plan["planned"] >= 1
        result = await service.reindex(embedder=embedder.embed)
        assert result["dry_run"] is False and result["processed"] >= 1
        again = await service.reindex(embedder=embedder.embed)
        assert again["processed"] == 0
        # ---- 过期维护 ----
        expiring = await service.persist_governed(
            case_id=None,
            request=CreateMemoryRequest(
                scope="case",
                kind="preference",
                content="短期偏好：本周内使用红色标注。",
                source_type="user",
                source_id="turn-5",
            ),
            memory_type=MEMORY_TYPE_OPERATOR_PREFERENCE,
            trust_level=TRUST_OPERATOR_INPUT,
            explicit_user_input=True,
            has_evidence=True,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        maintenance = await service.maintenance()
        assert maintenance["expired"] >= 1
        active = await knowledge.list_memories(None, status=MEMORY_STATUS_ACTIVE)
        assert all(record.id != expiring.id for record in active)

    async def _main() -> None:
        await run()
        await database.dispose()

    asyncio.run(_main())
    _cleanup_db("mem_gov_lifecycle.db")


def test_memory_governance_api() -> None:
    """一个 app 覆盖：筛选/禁用/恢复/修正/历史/秘密拒绝/reindex/删除不可检索。"""
    _cleanup_db("mem_gov_api.db")
    app = create_app(
        Settings(
            database_url=_db_url("mem_gov_api.db"),
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case = client.post(
            "/api/v1/cases",
            json={"topic": "记忆治理", "platforms": ["weibo"]},
        ).json()
        case_id = case["id"]
        created = client.post(
            f"/api/v1/cases/{case_id}/memories",
            json={
                "kind": "correction",
                "content": "用户确认：涉事账号是 B。",
                "source_type": "user_correction",
                "source_id": "turn-1",
                "importance": 1,
            },
        )
        assert created.status_code == 201
        memory = created.json()
        assert memory["active"] is True
        memory_id = memory["id"]
        listed = client.get(
            "/api/v1/memories",
            params={"memory_type": "case_fact", "status": "active"},
        )
        assert listed.status_code == 200
        assert any(entry["id"] == memory_id for entry in listed.json())
        disabled = client.post(
            f"/api/v1/memories/{memory_id}:disable", json={"actor": "user"}
        )
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"
        again = client.post(
            f"/api/v1/memories/{memory_id}:disable", json={"actor": "user"}
        )
        assert again.status_code == 409
        restored = client.post(
            f"/api/v1/memories/{memory_id}:restore", json={"actor": "user"}
        )
        assert restored.status_code == 200
        assert restored.json()["status"] == "active"
        corrected = client.post(
            f"/api/v1/memories/{memory_id}:correct",
            json={"content": "更正：涉事账号是 C。", "actor": "user"},
        )
        assert corrected.status_code == 200
        assert corrected.json()["version"] == 2
        assert corrected.json()["supersedes_id"] == memory_id
        history = client.get(f"/api/v1/memories/{memory_id}/history")
        assert history.status_code == 200
        assert any(entry["action"] == "correct" for entry in history.json())
        secret = client.post(
            f"/api/v1/cases/{case_id}/memories",
            json={
                "kind": "fact",
                "content": "token=sk-abcdefghijklmnopqrstuvwxyz",
                "source_type": "user",
                "source_id": "turn-9",
            },
        )
        assert secret.status_code == 400
        assert secret.json()["code"] == "memory_write_denied"
        plan = client.post(
            "/api/v1/memories/reindex",
            json={"dry_run": True, "limit": 50},
        )
        assert plan.status_code == 200
        assert plan.json()["dry_run"] is True
        maintenance = client.post("/api/v1/memories/maintenance")
        assert maintenance.status_code == 200
        assert "expired" in maintenance.json()
        pref = client.post(
            f"/api/v1/cases/{case_id}/memories",
            json={
                "kind": "preference",
                "content": "偏好：按时间倒序。",
                "source_type": "user",
                "source_id": "turn-10",
            },
        ).json()
        deleted = client.post(
            f"/api/v1/memories/{pref['id']}:delete", json={"actor": "user"}
        )
        assert deleted.status_code == 200
        search = client.post(
            f"/api/v1/cases/{case_id}/memory/search",
            json={"query": "按时间倒序", "limit": 5},
        )
        assert search.status_code == 200
        assert all(
            not hit["evidence_id"].endswith(":" + pref["id"]) for hit in search.json()
        )


