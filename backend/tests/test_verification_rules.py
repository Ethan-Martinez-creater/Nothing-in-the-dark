"""M7d verification rules: old-news reuse, authoritative whitelist and
the forced insufficient verdict when evidence is missing."""

from __future__ import annotations

from pathlib import Path

from app.application.repositories import ApplicationRepository
from app.infrastructure.database import Database
from app.schemas.cases import CreateCaseRequest
from app.services.analysis import verify_claims


def _post(post_id: str, content: str, *, author: str = "路人甲") -> dict:
    return {
        "id": post_id,
        "platform": "weibo",
        "author": author,
        "content": content,
        "published_at": "2026-08-01T00:00:00+00:00",
    }


async def test_default_path_forces_insufficient(tmp_path: Path) -> None:
    posts = [_post("p1", "官方回应称事故伤亡数据失实，正在调查")]
    result = await verify_claims(posts, "事故调查")
    card = result["cards"][0]
    assert card["verdict"] == "insufficient"
    assert card["verdict_label"] == "证据不足"
    assert "insufficient" in card["checks"]
    assert result["verification_checks"] == {
        "old_news": 0,
        "authoritative_source": 0,
        "insufficient": 1,
        "deduped": 0,
        "cross_platform": 0,
        "temporal_consistency": {"pass": 0, "fail": 0, "unknown": 1},
        "subject_consistency": {"pass": 0, "fail": 1, "unknown": 0},
        "context_consistency": {"pass": 1, "fail": 0, "unknown": 0},
    }


async def test_old_news_flagged_when_post_predates_case_window(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'verification.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(
                topic="事故调查",
                platforms=["weibo"],
                time_start="2026-08-05T00:00:00+00:00",
                time_end="2026-08-07T00:00:00+00:00",
            )
        )
        # Published 2026-08-01, four days before the case window opens.
        posts = [_post("p1", "官方回应称事故伤亡数据失实，正在调查")]
        result = await verify_claims(
            posts,
            "事故调查",
            repository=repository,
            case_id=case.id,
            created_by_run_id="run-1",
        )
        card = result["cards"][0]
        assert card["verdict"] == "old_news"
        assert card["verdict_label"] == "疑似旧闻新传"
        assert "old_news" in card["checks"]
        assert result["verification_checks"]["old_news"] == 1
    finally:
        await database.dispose()


async def test_authoritative_account_yields_credible_verdict(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'verification.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="事故调查", platforms=["weibo"])
        )
        await repository.upsert_account(
            case_id=None,
            platform="weibo",
            native_id="official",
            name="官方发言人",
            normalized_name="官方发言人",
            is_authoritative=True,
        )
        posts = [
            _post(
                "p1",
                "官方回应称事故伤亡数据失实，正在调查",
                author="官方发言人",
            )
        ]
        result = await verify_claims(
            posts,
            "事故调查",
            repository=repository,
            case_id=case.id,
            created_by_run_id="run-1",
        )
        card = result["cards"][0]
        assert card["verdict"] == "credible"
        assert card["verdict_label"] == "官方来源"
        assert "authoritative_source" in card["checks"]
        assert card["confidence"] == 0.6
    finally:
        await database.dispose()


async def test_whitelist_normalized_name_match_still_works(tmp_path: Path) -> None:
    """Names differing only by case/noise still hit the whitelist."""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'verification.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(topic="事故调查", platforms=["weibo"])
        )
        await repository.upsert_account(
            case_id=None,
            platform="weibo",
            native_id="official",
            name="官方发言人",
            normalized_name="官方发言人",
            is_authoritative=True,
        )
        posts = [
            _post(
                "p1",
                "官方回应称事故伤亡数据失实，正在调查",
                author="官方发言人_weibo",
            )
        ]
        result = await verify_claims(
            posts,
            "事故调查",
            repository=repository,
            case_id=case.id,
            created_by_run_id="run-1",
        )
        assert result["cards"][0]["verdict"] == "credible"
    finally:
        await database.dispose()


async def test_checks_aggregate_across_cards(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'verification.db'}")
    await database.create_schema()
    repository = ApplicationRepository(database)
    try:
        case = await repository.create_case(
            CreateCaseRequest(
                topic="事故调查",
                platforms=["weibo"],
                time_start="2026-08-05T00:00:00+00:00",
            )
        )
        posts = [
            _post("p1", "官方回应称事故伤亡数据失实，正在调查"),  # old news
            _post("p2", "网友质疑调查进展缓慢，要求公开数据"),  # old news too
        ]
        result = await verify_claims(
            posts,
            "事故调查",
            repository=repository,
            case_id=case.id,
            created_by_run_id="run-1",
        )
        assert len(result["cards"]) == 2
        assert result["verification_checks"]["old_news"] == 2
        assert result["verification_checks"]["insufficient"] == 0
    finally:
        await database.dispose()
