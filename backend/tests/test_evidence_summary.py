"""Evidence summary endpoint (案例证据汇总侧栏)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.database.social_repository import SocialRepository
from app.main import create_app
from app.schemas.cases import CreateCaseRequest


async def _seed(db_path: Path) -> str:
    """Seed a case with two claims (one verified) and five evidence rows:
    2+2 attached to claims, 1 unattached. Returns the case id."""
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="证据汇总", platforms=["weibo", "zhihu"])
        )
        run = await repository.create_agent_run(
            case_id=case.id,
            turn_id=None,
            objective="核查主张",
            metadata={},
        )
        first = await repository.create_claim(
            case_id=case.id,
            text="官方账号未发布相关公告",
            created_by_run_id=run.id,
        )
        second = await repository.create_claim(
            case_id=case.id,
            text="传播规模超过百万",
            created_by_run_id=run.id,
        )
        await repository.update_claim_verdict(
            second.id,
            verdict="refuted",
            status="verified",
            confidence=0.9,
        )
        # first claim 的 evidence：相关度 0.9（反驳）与 0.4（支持）。
        await repository.create_evidence(
            case_id=case.id,
            claim_id=first.id,
            source_type="post",
            source_id="post-1",
            stance="oppose",
            excerpt="官方账号发布辟谣声明",
            relevance=0.9,
        )
        await repository.create_evidence(
            case_id=case.id,
            claim_id=first.id,
            source_type="post",
            source_id="post-2",
            stance="support",
            excerpt="多账号引用旧闻",
            relevance=0.4,
        )
        await repository.create_evidence(
            case_id=case.id,
            claim_id=second.id,
            source_type="comment",
            source_id="comment-1",
            stance="oppose",
            excerpt="实际互动量不足十万",
            relevance=0.85,
        )
        await repository.create_evidence(
            case_id=case.id,
            claim_id=second.id,
            source_type="comment",
            source_id="comment-2",
            stance="context",
            excerpt="各平台口径不一",
            relevance=0.3,
        )
        await repository.create_evidence(
            case_id=case.id,
            claim_id=None,
            source_type="profile",
            source_id="profile-1",
            stance="context",
            excerpt="无主张归属的背景资料",
            relevance=0.2,
        )
        return case.id
    finally:
        await database.dispose()


def test_api_groups_evidence_by_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence_summary.db"
    case_id = asyncio.run(_seed(db_path))
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True)
    )
    with TestClient(app) as client:
        response = client.get(f"/api/v1/cases/{case_id}/evidence-summary")
        assert response.status_code == 200
        payload = response.json()
        assert payload["case_id"] == case_id
        assert len(payload["claims"]) == 2

        by_text = {claim["text"]: claim for claim in payload["claims"]}
        first = by_text["官方账号未发布相关公告"]
        assert first["status"] == "open"
        assert first["verdict"] is None
        # evidence 按 relevance 降序。
        assert [item["relevance"] for item in first["evidence"]] == [0.9, 0.4]
        assert [item["stance"] for item in first["evidence"]] == ["oppose", "support"]
        assert first["evidence"][0]["source_type"] == "post"
        assert first["evidence"][0]["source_id"] == "post-1"
        assert first["evidence"][0]["excerpt"] == "官方账号发布辟谣声明"

        second = by_text["传播规模超过百万"]
        assert second["status"] == "verified"
        assert second["verdict"] == "refuted"
        assert second["confidence"] == 0.9
        assert [item["stance"] for item in second["evidence"]] == ["oppose", "context"]

        # 无 claim 归属的 evidence 单独列出。
        assert len(payload["unassigned"]) == 1
        assert payload["unassigned"][0]["source_id"] == "profile-1"
        assert payload["unassigned"][0]["claim_id"] is None


def test_api_empty_case_returns_empty_lists(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence_empty.db"
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True)
    )
    with TestClient(app) as client:
        case_id = client.post(
            "/api/v1/cases",
            json={"topic": "空案例", "platforms": ["weibo"]},
        ).json()["id"]
        response = client.get(f"/api/v1/cases/{case_id}/evidence-summary")
        assert response.status_code == 200
        payload = response.json()
        assert payload["claims"] == []
        assert payload["unassigned"] == []


def test_api_unknown_case_returns_404(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'evidence_missing.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/cases/no-such-case/evidence-summary")
        assert response.status_code == 404


def test_api_appends_collected_posts_to_unassigned(tmp_path: Path) -> None:
    """采集帖子即使尚未核查也应出现在证据侧栏（unassigned，social_post）。"""
    db_path = tmp_path / "evidence_posts.db"
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    asyncio.run(database.create_schema())
    repository = ApplicationRepository(database)
    social = SocialRepository(database)

    async def seed() -> str:
        case = await repository.create_case(
            CreateCaseRequest(topic="帖子证据", platforms=["weibo"])
        )
        await social.persist_batch(
            case_id=case.id,
            posts=[
                {
                    "id": "weibo-abc",
                    "platform": "weibo",
                    "author": "现场观察员",
                    "content": "最早的现场信息提到了竹知了。",
                    "published_at": "2026-08-01T00:00:00+00:00",
                    "sentiment": "neutral",
                    "engagement": 120,
                    "url": "https://example.invalid/weibo/1",
                    "is_demo": True,
                },
            ],
        )
        await database.dispose()
        return case.id

    case_id = asyncio.run(seed())
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True)
    )
    with TestClient(app) as client:
        payload = client.get(f"/api/v1/cases/{case_id}/evidence-summary").json()
        assert payload["claims"] == []
        assert len(payload["unassigned"]) == 1
        item = payload["unassigned"][0]
        assert item["source_type"] == "social_post"
        assert item["stance"] == "context"
        assert "现场信息" in item["excerpt"]
        assert item["metadata_json"]["platform"] == "weibo"
        assert item["metadata_json"]["author"] == "现场观察员"


def test_api_dedupes_posts_already_cited_by_evidence(tmp_path: Path) -> None:
    """核查证据行已引用的帖子不再重复进 unassigned。"""
    db_path = tmp_path / "evidence_dedupe.db"
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    asyncio.run(database.create_schema())
    repository = ApplicationRepository(database)
    social = SocialRepository(database)

    async def seed() -> str:
        case = await repository.create_case(
            CreateCaseRequest(topic="去重", platforms=["weibo"])
        )
        run = await repository.create_agent_run(
            case_id=case.id, turn_id=None, objective="核查", metadata={},
        )
        claim = await repository.create_claim(
            case_id=case.id, text="主张", created_by_run_id=run.id,
        )
        await repository.create_evidence(
            case_id=case.id,
            claim_id=claim.id,
            source_type="post",
            source_id="weibo-abc",
            stance="support",
            excerpt="核查引用",
            relevance=0.9,
        )
        await social.persist_batch(
            case_id=case.id,
            posts=[
                {
                    "id": "weibo-abc",
                    "platform": "weibo",
                    "author": "现场观察员",
                    "content": "同一条帖子",
                    "published_at": "2026-08-01T00:00:00+00:00",
                    "sentiment": "neutral",
                    "engagement": 10,
                    "url": "https://example.invalid/weibo/1",
                    "is_demo": True,
                },
            ],
        )
        await database.dispose()
        return case.id

    case_id = asyncio.run(seed())
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True)
    )
    with TestClient(app) as client:
        payload = client.get(f"/api/v1/cases/{case_id}/evidence-summary").json()
        assert payload["claims"][0]["evidence"][0]["source_id"] == "weibo-abc"
        assert payload["unassigned"] == []
