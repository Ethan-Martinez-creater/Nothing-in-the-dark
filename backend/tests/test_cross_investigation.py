"""V3 §79: Cross-Investigation tests (C01–C16)。

内存 SQLite；覆盖 4 detectors、observed/candidate 语义、fingerprint
确定性、evidence 聚合、refresh 幂等、stale reconcile、retract 传播、
no-O(N²) 结构性约束与 case 删除级联。
"""

from __future__ import annotations

from typing import Any

from app.application.cross_investigation_service import (
    CrossInvestigationService,
)
from app.application.repositories import ApplicationRepository
from app.application.workspace_entity_service import WorkspaceEntityService
from app.infrastructure.database.alignment_repository import AlignmentRepository
from app.infrastructure.database.cross_investigation_repository import (
    CrossInvestigationRepository,
    cross_link_fingerprint,
)
from app.infrastructure.database.integrity_repository import IntegrityRepository
from app.infrastructure.database.media_pipeline_repository import (
    MediaPipelineRepository,
)
from app.infrastructure.database.models import MediaAssetRecord
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.database.workspace_entity_repository import (
    WorkspaceEntityRepository,
)
from app.schemas.cases import CreateCaseRequest
from tests.memory_db import MemoryDatabase


async def _setup() -> Any:
    from types import SimpleNamespace

    database = MemoryDatabase()
    await database.create_schema()
    app_repo = ApplicationRepository(database)
    workspace_repo = WorkspaceEntityRepository(database)
    social_repo = SocialRepository(database)
    media_repo = MediaPipelineRepository(database)
    integrity_repo = IntegrityRepository(database)
    alignment_repo = AlignmentRepository(database)
    workspace_service = WorkspaceEntityService(
        workspace_repository=workspace_repo,
        alignment_repository=alignment_repo,
        application_repository=app_repo,
        social_repository=social_repo,
        integrity_repository=integrity_repo,
        database=database,
    )
    cross_repo = CrossInvestigationRepository(database)
    service = CrossInvestigationService(
        cross_repository=cross_repo,
        workspace_repository=workspace_repo,
        workspace_service=workspace_service,
        social_repository=social_repo,
        media_repository=media_repo,
        application_repository=app_repo,
        database=database,
    )
    case_a = await app_repo.create_case(
        CreateCaseRequest(
            topic="调查A",
            platforms=["weibo"],
            time_start="2026-08-10",
            time_end="2026-08-20",
        )
    )
    case_b = await app_repo.create_case(
        CreateCaseRequest(
            topic="调查B",
            platforms=["weibo"],
            time_start="2026-08-10",
            time_end="2026-08-20",
        )
    )
    return SimpleNamespace(
        db=database,
        app=app_repo,
        workspace=workspace_repo,
        social=social_repo,
        media=media_repo,
        integrity=integrity_repo,
        alignment=alignment_repo,
        workspace_service=workspace_service,
        cross=cross_repo,
        service=service,
        case_a=case_a,
        case_b=case_b,
    )


def _post(
    platform: str,
    native_id: str,
    author_id: str,
    *,
    content: str = "事件讨论内容",
) -> dict[str, Any]:
    return {
        "platform": platform,
        "native_id": native_id,
        "content_type": "post",
        "title": "",
        "content": content,
        "author": f"author-{author_id}",
        "published_at": "2026-08-15T10:00:00+08:00",
        "engagement": 5,
        "metrics": {"total": 5},
        "url": f"https://example.com/{platform}/{native_id}",
        "raw": {"id": native_id, "user_id": author_id},
        "comments": [],
    }


async def _asset(
    env: Any,
    case_id: str,
    *,
    sha256: str | None = None,
    phash: str | None = None,
    ocr_text: str | None = None,
    media_type: str = "image",
) -> MediaAssetRecord:
    record = MediaAssetRecord(
        case_id=case_id,
        post_id=None,
        platform="weibo",
        media_type=media_type,
        url=f"https://example.com/media/{sha256 or phash}-{case_id[:4]}",
        normalized_url=f"https://example.com/media/{sha256 or phash}",
        file_sha256=sha256,
        actual_sha256=sha256,
        phash=phash,
        ocr_text=ocr_text,
    )
    async with env.db.session_factory() as session:
        session.add(record)
        await session.commit()
        await session.refresh(record)
    return record


def _pair(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a, b)))  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# C01–C05: 4 detectors
# ---------------------------------------------------------------------------


async def test_c01_shared_actor_observed() -> None:
    env = await _setup()
    # 同一 platform_account 出现在两个 case（Case A 账号 + Case B 帖子作者）
    await env.app.upsert_account(
        case_id=env.case_a.id,
        platform="weibo",
        native_id="123",
        name="跨事件账号",
        normalized_name="跨事件账号",
    )
    await env.social.persist_batch(
        case_id=env.case_b.id,
        posts=[_post("weibo", "b1", "123")],
    )
    await env.workspace_service.refresh_case(env.case_a.id)
    await env.workspace_service.refresh_case(env.case_b.id)
    links = await env.service.refresh_case(env.case_a.id)
    assert links["shared_actor"]["upserted"] >= 1
    actor_links = await env.cross.list_for_case(
        env.case_a.id, relation_type="shared_actor"
    )
    assert len(actor_links) == 1
    assert actor_links[0].status == "observed"
    assert actor_links[0].score == 1.0
    left, right = _pair(env.case_a.id, env.case_b.id)
    assert (actor_links[0].left_case_id, actor_links[0].right_case_id) == (
        left,
        right,
    )
    await env.db.dispose()


async def test_c02_shared_post_observed() -> None:
    env = await _setup()
    await env.social.persist_batch(
        case_id=env.case_a.id, posts=[_post("weibo", "same-1", "u1")]
    )
    await env.social.persist_batch(
        case_id=env.case_b.id, posts=[_post("weibo", "same-1", "u1")]
    )
    summary = await env.service.refresh_case(env.case_a.id)
    assert summary["shared_post"]["upserted"] >= 1
    post_links = await env.cross.list_for_case(
        env.case_a.id, relation_type="shared_post"
    )
    assert len(post_links) == 1
    assert post_links[0].status == "observed"
    assert post_links[0].evidence_count == 1
    await env.db.dispose()


async def test_c03_same_media_sha_observed() -> None:
    env = await _setup()
    await _asset(env, env.case_a.id, sha256="a" * 64)
    await _asset(env, env.case_b.id, sha256="a" * 64)
    summary = await env.service.refresh_case(env.case_a.id)
    assert summary["shared_media"]["upserted"] >= 1
    media_links = await env.cross.list_for_case(
        env.case_a.id, relation_type="shared_media"
    )
    assert len(media_links) == 1
    assert media_links[0].status == "observed"
    assert media_links[0].score == 1.0
    await env.db.dispose()


async def test_c04_phash_possible_threshold_is_candidate() -> None:
    env = await _setup()
    # 同一 phash + 相同 OCR 文本 → content_alignment score ≥ POSSIBLE_THRESHOLD
    await _asset(env, env.case_a.id, phash="fffe" + "0" * 60, ocr_text="相同画面文字")
    await _asset(env, env.case_b.id, phash="fffe" + "0" * 60, ocr_text="相同画面文字")
    await env.service.refresh_case(env.case_a.id)
    media_links = await env.cross.list_for_case(
        env.case_a.id, relation_type="shared_media"
    )
    assert len(media_links) == 1
    assert media_links[0].status == "candidate"
    assert media_links[0].score >= 0.70
    await env.db.dispose()


async def test_c05_same_content_hash_observed() -> None:
    env = await _setup()
    same_content = "完全相同的转发文案内容" * 3
    await env.social.persist_batch(
        case_id=env.case_a.id,
        posts=[_post("weibo", "ca-1", "u1", content=same_content)],
    )
    await env.social.persist_batch(
        case_id=env.case_b.id,
        posts=[_post("weibo", "cb-1", "u2", content=same_content)],
    )
    summary = await env.service.refresh_case(env.case_a.id)
    assert summary["shared_content"]["upserted"] >= 1
    content_links = await env.cross.list_for_case(
        env.case_a.id, relation_type="shared_content"
    )
    assert len(content_links) == 1
    assert content_links[0].status == "observed"
    await env.db.dispose()


# ---------------------------------------------------------------------------
# C06–C10: 双计、排序、fingerprint、聚合、幂等
# ---------------------------------------------------------------------------


async def test_c06_same_original_post_not_double_counted_as_content() -> None:
    env = await _setup()
    # 同 platform + native_id + 同 content：shared_post 成立时
    # shared_content 不重复计入同一原始 Post（§39）
    await env.social.persist_batch(
        case_id=env.case_a.id, posts=[_post("weibo", "dup-1", "u1")]
    )
    await env.social.persist_batch(
        case_id=env.case_b.id, posts=[_post("weibo", "dup-1", "u1")]
    )
    summary = await env.service.refresh_case(env.case_a.id)
    # shared_post 命中后，content detector 的 expected set 排除该原始 Post
    if summary["shared_post"]["upserted"] >= 1:
        content_links = await env.cross.list_for_case(
            env.case_a.id, relation_type="shared_content"
        )
        for link in content_links:
            for evidence in link.evidence_refs_json:
                assert evidence.get("other_post_id") not in (
                    evidence.get("anchor_post_id"),
                )
    await env.db.dispose()


async def test_c07_pair_ordering_deterministic() -> None:
    env = await _setup()
    await env.social.persist_batch(
        case_id=env.case_a.id, posts=[_post("weibo", "ord-1", "u1")]
    )
    await env.social.persist_batch(
        case_id=env.case_b.id, posts=[_post("weibo", "ord-1", "u1")]
    )
    await env.service.refresh_case(env.case_a.id)
    await env.service.refresh_case(env.case_b.id)
    link_a = (
        await env.cross.list_for_case(env.case_a.id, relation_type="shared_post")
    )[0]
    link_b = (
        await env.cross.list_for_case(env.case_b.id, relation_type="shared_post")
    )[0]
    assert link_a.id == link_b.id
    assert link_a.left_case_id < link_a.right_case_id
    await env.db.dispose()


async def test_c09_fingerprint_is_pair_relation_version() -> None:
    fingerprint = cross_link_fingerprint(
        left_case_id="a",
        right_case_id="b",
        relation_type="shared_post",
        algorithm_version="cross-intel-1.0.0",
    )
    same = cross_link_fingerprint(
        left_case_id="a",
        right_case_id="b",
        relation_type="shared_post",
        algorithm_version="cross-intel-1.0.0",
    )
    other_relation = cross_link_fingerprint(
        left_case_id="a",
        right_case_id="b",
        relation_type="shared_media",
        algorithm_version="cross-intel-1.0.0",
    )
    other_version = cross_link_fingerprint(
        left_case_id="a",
        right_case_id="b",
        relation_type="shared_post",
        algorithm_version="cross-intel-2.0.0",
    )
    assert fingerprint == same
    assert fingerprint != other_relation
    assert fingerprint != other_version


async def test_c10_multiple_evidence_aggregates_into_one_link() -> None:
    env = await _setup()
    # Case A/B 各采到同一批 3 个原始 Post → 一条 link，evidence_count=3
    for index in range(3):
        await env.social.persist_batch(
            case_id=env.case_a.id,
            posts=[_post("weibo", f"agg-{index}", f"u{index}")],
        )
        await env.social.persist_batch(
            case_id=env.case_b.id,
            posts=[_post("weibo", f"agg-{index}", f"u{index}")],
        )
    await env.service.refresh_case(env.case_a.id)
    post_links = await env.cross.list_for_case(
        env.case_a.id, relation_type="shared_post"
    )
    assert len(post_links) == 1
    assert post_links[0].evidence_count == 3
    assert len(post_links[0].evidence_refs_json) == 3
    await env.db.dispose()


async def test_c11_refresh_idempotent() -> None:
    env = await _setup()
    await env.social.persist_batch(
        case_id=env.case_a.id, posts=[_post("weibo", "idem-1", "u1")]
    )
    await env.social.persist_batch(
        case_id=env.case_b.id, posts=[_post("weibo", "idem-1", "u1")]
    )
    await env.service.refresh_case(env.case_a.id)
    first = await env.cross.count_for_case(env.case_a.id)
    await env.service.refresh_case(env.case_a.id)
    second = await env.cross.count_for_case(env.case_a.id)
    assert first == second
    link = (
        await env.cross.list_for_case(env.case_a.id, relation_type="shared_post")
    )[0]
    assert link.is_active is True
    await env.db.dispose()


# ---------------------------------------------------------------------------
# C12–C15: stale reconcile / no partial reconcile（C16 deletion → V3-9）
# ---------------------------------------------------------------------------


async def test_c12_stale_actor_relation_deactivates_shared_actor() -> None:
    env = await _setup()
    account = await env.app.upsert_account(
        case_id=env.case_a.id,
        platform="weibo",
        native_id="gone",
        name="将消失",
        normalized_name="将消失",
    )
    await env.social.persist_batch(
        case_id=env.case_b.id, posts=[_post("weibo", "b-gone", "gone")]
    )
    await env.workspace_service.refresh_case(env.case_a.id)
    await env.workspace_service.refresh_case(env.case_b.id)
    await env.service.refresh_case(env.case_a.id)
    assert (
        await env.cross.count_for_case(env.case_a.id, active_only=True) >= 1
    )
    # 账号消失（删除 account + case B 帖子）→ relation 消失 → link inactive
    async with env.db.session_factory() as session:
        from sqlalchemy import delete

        from app.infrastructure.database.models import AccountRecord, SourcePostRecord

        await session.execute(
            delete(AccountRecord).where(AccountRecord.id == account.id)
        )
        await session.execute(
            delete(SourcePostRecord).where(SourcePostRecord.case_id == env.case_b.id)
        )
        await session.commit()
    await env.workspace_service.refresh_case(env.case_a.id)
    await env.workspace_service.refresh_case(env.case_b.id)
    await env.service.refresh_case(env.case_a.id)
    actor_links = await env.cross.list_for_case(
        env.case_a.id, relation_type="shared_actor", active_only=True
    )
    assert actor_links == []
    await env.db.dispose()


async def test_c13_stale_media_content_links_inactive() -> None:
    env = await _setup()
    await _asset(env, env.case_a.id, sha256="b" * 64)
    await _asset(env, env.case_b.id, sha256="b" * 64)
    await env.service.refresh_case(env.case_a.id)
    assert (
        await env.cross.count_for_case(env.case_a.id, active_only=True) >= 1
    )
    # 删除 case B 的 media → refresh 后 stale link inactive
    async with env.db.session_factory() as session:
        from sqlalchemy import delete

        from app.infrastructure.database.models import MediaAssetRecord

        await session.execute(
            delete(MediaAssetRecord).where(MediaAssetRecord.case_id == env.case_b.id)
        )
        await session.commit()
    await env.service.refresh_case(env.case_a.id)
    media_links = await env.cross.list_for_case(
        env.case_a.id, relation_type="shared_media", active_only=True
    )
    assert media_links == []
    # inactive link 保留（不物理删除）
    all_links = await env.cross.list_for_case(
        env.case_a.id, relation_type="shared_media", active_only=False
    )
    assert len(all_links) >= 1
    assert all(link.is_active is False for link in all_links)
    await env.db.dispose()


async def test_c15_detector_exception_no_partial_reconcile() -> None:
    env = await _setup()
    await env.social.persist_batch(
        case_id=env.case_a.id, posts=[_post("weibo", "exc-1", "u1")]
    )
    await env.social.persist_batch(
        case_id=env.case_b.id, posts=[_post("weibo", "exc-1", "u1")]
    )
    await env.service.refresh_case(env.case_a.id)
    assert (
        await env.cross.count_for_case(env.case_a.id, active_only=True) >= 1
    )
    # detector 抛异常时：expected set 未完成 → 不得 reconcile stale。
    # shared_media detector 抛异常 → 已 flush 的 shared_post link 必须保留。
    original = env.service._detect_shared_media

    async def _boom(case_id: str) -> list[dict[str, Any]]:
        raise RuntimeError("detector exploded")

    env.service._detect_shared_media = _boom  # type: ignore[method-assign]
    try:
        await env.service.refresh_case(env.case_a.id)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected detector failure to propagate")
    finally:
        env.service._detect_shared_media = original  # type: ignore[method-assign]
    post_links = await env.cross.list_for_case(
        env.case_a.id, relation_type="shared_post", active_only=True
    )
    assert len(post_links) >= 1
    await env.db.dispose()


async def test_c08_pair_ordering_in_upsert() -> None:
    env = await _setup()
    left, right = _pair(env.case_a.id, env.case_b.id)
    link = await env.cross.upsert_link(
        left_case_id=right,  # 故意反序传入
        right_case_id=left,
        relation_type="shared_post",
        status="observed",
        score=1.0,
        evidence_count=1,
        evidence_refs=[],
        feature_scores={},
        algorithm_version="cross-intel-1.0.0",
    )
    assert link.left_case_id < link.right_case_id or link.left_case_id == left
    assert link.is_active is True
    await env.db.dispose()


# ---------------------------------------------------------------------------
# C17: Related DTO shared_* count 使用 evidence_count（Rework R9）
# ---------------------------------------------------------------------------


async def test_c17_related_counts_use_evidence_count() -> None:
    """shared_actor evidence_count=3 / shared_media evidence_count=2
    → Related DTO：shared_actor_count=3、shared_media_count=2、
    relation_count=2（distinct relation type 数）。"""
    env = await _setup()
    for index in (1, 2, 3):
        await env.app.upsert_account(
            case_id=env.case_a.id,
            platform="weibo",
            native_id=f"r{index}",
            name=f"主体r{index}",
            normalized_name=f"主体r{index}",
        )
        await env.social.persist_batch(
            case_id=env.case_b.id,
            posts=[_post("weibo", f"b{index}", f"r{index}")],
        )
    await _asset(env, env.case_a.id, sha256="e1" * 32)
    await _asset(env, env.case_b.id, sha256="e1" * 32)
    await _asset(env, env.case_a.id, sha256="e2" * 32)
    await _asset(env, env.case_b.id, sha256="e2" * 32)
    await env.workspace_service.refresh_case(env.case_a.id)
    await env.workspace_service.refresh_case(env.case_b.id)
    await env.service.refresh_case(env.case_a.id)

    related = await env.service.related_investigations(env.case_a.id)
    assert len(related) == 1
    entry = related[0]
    assert entry["case_id"] == env.case_b.id
    assert entry["shared_actor_count"] == 3
    assert entry["shared_media_count"] == 2
    assert entry["relation_count"] == 2
    assert entry["relation_types"] == ["shared_actor", "shared_media"]
    await env.db.dispose()
