"""Tests for cross-platform alignment (06)."""

from __future__ import annotations

import asyncio
import atexit
import shutil
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.alignment_service import AlignmentService
from app.application.repositories import ApplicationRepository
from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.database.alignment_repository import AlignmentRepository
from app.infrastructure.database.media_pipeline_repository import MediaPipelineRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.main import create_app
from app.schemas.cases import CreateCaseRequest
from app.services import alignment as algo

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-align-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


# ---------- 规范化 ---------------------------------------------------------


def test_normalize_name_fullwidth() -> None:
    assert algo.normalize_name("ＡＢＣ　ｘｙｚ") == "abcxyz"
    assert algo.normalize_name("Official  Account") == "officialaccount"


def test_normalize_url_strips_params() -> None:
    # 只移除白名单跟踪参数（from），path 保留大小写。
    assert algo.normalize_url("https://x.com/p/1?from=a#frag") == "https://x.com/p/1"
    # scheme/host 小写，path 保留大小写（不再合并区分大小写的资源）。
    assert algo.normalize_url("HTTPS://X.COM/P/1/") == "https://x.com/P/1"
    # 非跟踪参数保留。
    assert algo.normalize_url("https://x.com/p?id=42") == "https://x.com/p?id=42"


def test_jaccard_similarity() -> None:
    assert algo.jaccard_similarity("完全相同的内容", "完全相同的内容") == 1.0
    assert algo.jaccard_similarity("完全不同", "毫无关系") == 0.0
    assert 0.0 < algo.jaccard_similarity("部分相似文本甲", "部分相似文本乙") < 1.0


def test_minhash_jaccard_approximation() -> None:
    sig_a = algo.minhash_signature("这是一段较长的中文文本，用于测试 MinHash 相似度", k=64)
    sig_b = algo.minhash_signature("这是一段较长的中文文本，用于测试 MinHash 相似度", k=64)
    assert algo.minhash_jaccard(sig_a, sig_b) == 1.0
    sig_c = algo.minhash_signature("完全不相关的另一段英文 text content here", k=64)
    assert algo.minhash_jaccard(sig_a, sig_c) < 0.5


# ---------- 账号/内容对齐 --------------------------------------------------


def test_account_alignment_same_name_weak() -> None:
    # 仅显示名相同 + 无头像 + 认证一致：不会到 confirmed。
    result = algo.account_alignment(
        {"name": "官方发布", "verified": True},
        {"name": "官方发布", "verified": True},
    )
    decision, _ = algo.decide_relation(
        relation_type="same_as", score=result["score"], features=result["features"]
    )
    assert decision in ("probable", "possible", "pending")
    assert decision != "confirmed"


def test_account_alignment_verified_conflict_lowers_score() -> None:
    consistent = algo.account_alignment(
        {"name": "同名账号", "verified": True},
        {"name": "同名账号", "verified": True},
    )
    conflicting = algo.account_alignment(
        {"name": "同名账号", "verified": True},
        {"name": "同名账号", "verified": False},
    )
    assert conflicting["score"] < consistent["score"]


def test_content_alignment_sha256_confirmed() -> None:
    result = algo.content_alignment(
        {"sha256": "abc123", "content": "x"},
        {"sha256": "abc123", "content": "y"},
    )
    decision, _ = algo.decide_relation(
        relation_type="same_as", score=result["score"], features=result["features"]
    )
    assert result["features"]["sha256_match"] == 1.0
    assert decision == "confirmed"


def test_decide_thresholds() -> None:
    assert (
        algo.decide_relation(
            relation_type="same_as", score=0.96, features={}
        )[0]
        == "probable"
    )
    assert (
        algo.decide_relation(
            relation_type="same_as", score=0.80, features={}
        )[0]
        == "possible"
    )
    assert (
        algo.decide_relation(
            relation_type="same_as", score=0.50, features={}
        )[0]
        == "pending"
    )


def test_undirected_key_symmetry() -> None:
    assert algo.undirected_key("a", "1", "b", "2") == ("a:1", "b:2")
    assert algo.undirected_key("b", "2", "a", "1") == ("a:1", "b:2")


# ---------- 仓储 -----------------------------------------------------------


async def _setup_repo(db_path: Path) -> tuple[AlignmentRepository, str]:
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(CreateCaseRequest(topic="对齐测试", platforms=["weibo"]))
    return AlignmentRepository(database), case.id


async def test_candidate_undirected_dedup() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    first = await repo.create_alignment_candidate(
        case_id=case_id,
        left_type="media:image",
        left_id="a",
        right_type="media:image",
        right_id="b",
        combined_score=0.9,
        decision="possible",
    )
    assert first is not None
    # 反向 A-B 与 B-A 重复。
    reverse = await repo.create_alignment_candidate(
        case_id=case_id,
        left_type="media:image",
        left_id="b",
        right_type="media:image",
        right_id="a",
        combined_score=0.9,
        decision="possible",
    )
    assert reverse is None


async def test_candidate_review_flow() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    candidate = await repo.create_alignment_candidate(
        case_id=case_id,
        left_type="account",
        left_id="x",
        right_type="account",
        right_id="y",
        decision="probable",
    )
    assert candidate is not None
    rejected = await repo.set_candidate_decision(candidate.id, "rejected")
    assert rejected.decision == "rejected"
    reopened = await repo.set_candidate_decision(candidate.id, "pending")
    assert reopened.decision == "pending"


async def test_alignment_service_analyze() -> None:
    db_path = _tmp_db()
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(
        CreateCaseRequest(topic="对齐分析", platforms=["weibo", "bilibili"])
    )
    # 两个跨平台账号：昵称 + 头像 pHash 均相同 → 生成 probable 候选。
    await app_repo.upsert_account(
        case_id=case.id, platform="weibo", native_id="w1",
        name="官方发布", normalized_name="官方发布", verified=True,
        metadata={"avatar_phash": "abc123"},
    )
    await app_repo.upsert_account(
        case_id=case.id, platform="bilibili", native_id="b1",
        name="官方发布", normalized_name="官方发布", verified=True,
        metadata={"avatar_phash": "abc123"},
    )
    # 同名但无头像的账号不生成自动候选（显示名相同不能证明同一）。
    await app_repo.upsert_account(
        case_id=case.id, platform="zhihu", native_id="z1",
        name="官方发布", normalized_name="官方发布", verified=True,
    )
    repo = AlignmentRepository(database)
    media_repo = MediaPipelineRepository(database)
    service = AlignmentService(repo, media_repo, app_repo)
    result = await service.analyze_case(case.id)
    assert result["account_candidates"] >= 1
    candidates = await repo.list_candidates(case.id)
    assert len(candidates) >= 1
    assert all(c.decision in ("probable", "possible", "confirmed") for c in candidates)
    # 同名无头像的账号对不应进入候选。
    assert not any(
        "z1" in (c.left_id, c.right_id) for c in candidates
    )


# ---------- API -----------------------------------------------------------


def test_api_alignment_candidates() -> None:
    db_path = _tmp_db()
    database = Database(f"sqlite+aiosqlite:///{db_path}")
    asyncio.run(database.create_schema())
    app_repo = ApplicationRepository(database)

    async def seed() -> str:
        case = await app_repo.create_case(CreateCaseRequest(topic="对齐 API", platforms=["weibo"]))
        repo = AlignmentRepository(database)
        await repo.create_alignment_candidate(
            case_id=case.id,
            left_type="media:image", left_id="a",
            right_type="media:image", right_id="b",
            combined_score=0.9, decision="possible",
        )
        return case.id

    case_id = asyncio.run(seed())
    asyncio.run(database.dispose())

    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True)
    )
    with TestClient(app) as client:
        response = client.get(f"/api/v1/cases/{case_id}/alignments/candidates")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 1
        candidate_id = payload[0]["id"]
        # confirm 端点。
        confirmed = client.post(
            f"/api/v1/cases/{case_id}/alignments/{candidate_id}:confirm", json={}
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["decision"] == "confirmed"


async def test_candidates_are_versioned() -> None:
    repo, case_id = await _setup_repo(_tmp_db())
    first = await repo.create_alignment_candidate(
        case_id=case_id,
        left_type="post",
        left_id="a",
        right_type="post",
        right_id="b",
        model_version="v1",
    )
    second = await repo.create_alignment_candidate(
        case_id=case_id,
        left_type="post",
        left_id="a",
        right_type="post",
        right_id="b",
        model_version="v2",
    )
    assert first is not None and second is not None
    assert first.id != second.id


async def test_post_alignment_is_review_only_until_calibrated() -> None:
    database = Database(f"sqlite+aiosqlite:///{_tmp_db()}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(
        CreateCaseRequest(topic="内容对齐", platforms=["weibo", "bilibili"])
    )
    social = SocialRepository(database)
    await social.persist_batch(
        case_id=case.id,
        posts=[
            {
                "platform": "weibo",
                "native_id": "w-post",
                "content": "这是一段跨平台完全相同且足够长的测试内容",
            },
            {
                "platform": "bilibili",
                "native_id": "b-post",
                "content": "这是一段跨平台完全相同且足够长的测试内容",
            },
        ],
    )
    repo = AlignmentRepository(database)
    service = AlignmentService(
        repo, MediaPipelineRepository(database), app_repo, social
    )
    result = await service.analyze_case(case.id)
    assert result["post_candidates"] == 1
    candidates = await repo.list_candidates(case.id)
    post_candidate = next(candidate for candidate in candidates if candidate.left_type == "post")
    assert post_candidate.decision == "possible"
    assert post_candidate.feature_scores["publication_gate"] == "closed_uncalibrated"


async def test_reopen_retracts_only_materialized_relationships() -> None:
    database = Database(f"sqlite+aiosqlite:///{_tmp_db()}")
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    case = await app_repo.create_case(
        CreateCaseRequest(topic="撤销关系", platforms=["weibo", "bilibili"])
    )
    repo = AlignmentRepository(database)
    service = AlignmentService(repo, MediaPipelineRepository(database), app_repo)
    candidate = await repo.create_alignment_candidate(
        case_id=case.id,
        left_type="post",
        left_id="post-a",
        right_type="post",
        right_id="post-b",
        decision="confirmed",
    )
    assert candidate is not None
    family = await service.materialize_candidate(case.id, candidate.id)
    assert len(await repo.list_family_members(family.id)) == 2
    await service.retract_candidate(case.id, candidate.id)
    assert await repo.list_family_members(family.id) == []
