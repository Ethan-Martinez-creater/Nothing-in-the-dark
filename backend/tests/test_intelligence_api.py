"""V3 §23: Intelligence API tests（quality 部分；后续 V3 模块在此追加）。

用独立 FastAPI app + dependency_overrides 注入 fake container，
不依赖完整 ApplicationContainer 启动。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import dependencies
from app.api.routes import quality as quality_routes
from app.core.errors import register_exception_handlers


def _fake_payload() -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "case_id": "case-1",
        "overall_score": 82.5,
        "grade": "acceptable",
        "dimensions": [
            {
                "key": "collection_coverage",
                "label": "Collection Coverage",
                "weight": 25,
                "score": 100.0,
                "available": True,
                "metrics": {},
            },
            {
                "key": "evidence_coverage",
                "label": "Evidence Coverage",
                "weight": 25,
                "score": 65.0,
                "available": True,
                "metrics": {},
            },
        ],
        "gaps": [
            {
                "code": "claim_without_evidence",
                "severity": "warning",
                "object_type": "claim",
                "object_id": None,
                "message": "1 个主张未绑定任何 Evidence。",
                "action": {
                    "type": "navigate",
                    "target": "/investigations/case-1/evidence",
                },
            }
        ],
        "warnings": [],
        "disclaimer": "Quality Score 表示调查完整度与准备度，不代表事实真实性。",
        "computed_at": now,
        "algorithm_version": "quality-1.0.0",
        "input_fingerprint": "fp-1",
    }


class _FakeQuality:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def evaluate(self, case_id: str, *, force: bool = False) -> dict[str, Any]:
        self.calls.append((case_id, force))
        return _fake_payload()


class _FakeContainer:
    def __init__(self) -> None:
        self.investigation_quality = _FakeQuality()


def _client() -> tuple[TestClient, _FakeContainer]:
    app = FastAPI()
    register_exception_handlers(app)
    container = _FakeContainer()
    app.dependency_overrides[dependencies.get_container] = lambda: container
    app.include_router(quality_routes.router, prefix="/cases")
    return TestClient(app), container


def test_quality_get_fresh_if_needed() -> None:
    client, container = _client()
    response = client.get("/cases/case-1/quality")
    assert response.status_code == 200
    payload = response.json()
    assert payload["case_id"] == "case-1"
    assert payload["grade"] == "acceptable"
    assert payload["disclaimer"].startswith("Quality Score")
    # fresh-if-needed：非 force
    assert container.investigation_quality.calls == [("case-1", False)]


def test_quality_refresh_forces_recompute() -> None:
    client, container = _client()
    response = client.post("/cases/case-1/quality:refresh")
    assert response.status_code == 200
    assert container.investigation_quality.calls == [("case-1", True)]


def test_quality_case_not_found() -> None:
    class _NotFoundQuality:
        async def evaluate(self, case_id: str, *, force: bool = False) -> dict[str, Any]:
            from app.core.errors import ResourceNotFoundError

            raise ResourceNotFoundError("case", case_id)

    app = FastAPI()
    register_exception_handlers(app)
    container = _FakeContainer()
    container.investigation_quality = _NotFoundQuality()
    app.dependency_overrides[dependencies.get_container] = lambda: container
    app.include_router(quality_routes.router, prefix="/cases")
    client = TestClient(app)
    response = client.get("/cases/missing/quality")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# V3 §33: Workspace Entity API
# ---------------------------------------------------------------------------


class _FakeEntityService:
    async def list_entities(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["limit"] <= 50
        return {
            "items": [
                {
                    "entity_id": "e-1",
                    "entity_type": "account",
                    "canonical_name": "账号甲",
                    "platforms": ["weibo"],
                    "investigation_count": 2,
                    "post_count": 5,
                    "comment_count": 1,
                    "last_seen_at": datetime.now(UTC),
                    "risk_summary": None,
                }
            ],
            "total": 1,
        }

    async def get_profile(self, entity_id: str) -> dict[str, Any]:
        return {
            "entity_id": entity_id,
            "component_key": entity_id,
            "entity_ids": [entity_id],
            "entity_type": "account",
            "canonical_name": "账号甲",
            "aliases": [],
            "platform_identities": [
                {"key_type": "platform_account", "key_value": "weibo:123"}
            ],
            "investigation_count": 2,
            "investigations": ["a", "b"],
            "post_count": 5,
            "comment_count": 1,
            "engagement_total": 50,
            "first_seen_at": datetime.now(UTC),
            "last_seen_at": datetime.now(UTC),
            "recent_posts": [],
            "risk_assessments": [],
            "unresolved_local_risk": [],
            "coordination_memberships": [],
            "algorithm_version": "workspace-entity-1.0.0",
        }

    async def list_case_entities(self, case_id: str, **kwargs: Any) -> dict[str, Any]:
        assert case_id == "case-1"
        return {"items": [], "total": 0}


def _entity_client() -> tuple[TestClient, _FakeContainer]:
    from app.api.routes import workspace_entities as entity_routes

    app = FastAPI()
    register_exception_handlers(app)
    container = _FakeContainer()
    container.workspace_entities = _FakeEntityService()
    app.dependency_overrides[dependencies.get_container] = lambda: container
    app.include_router(entity_routes.entities_router, prefix="/intelligence")
    app.include_router(entity_routes.case_router, prefix="/cases")
    return TestClient(app), container


def test_entity_list_with_filters() -> None:
    client, _ = _entity_client()
    response = client.get(
        "/intelligence/entities",
        params={"query": "账号", "min_investigations": 2, "limit": 10},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["canonical_name"] == "账号甲"


def test_entity_detail_profile() -> None:
    client, _ = _entity_client()
    response = client.get("/intelligence/entities/e-1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["component_key"] == "e-1"
    assert payload["platform_identities"][0]["key_value"] == "weibo:123"
    # candidate/risk 语义红线：无 risk 时不渲染 risk
    assert payload["risk_assessments"] == []


def test_case_entities_scoped() -> None:
    client, _ = _entity_client()
    response = client.get("/cases/case-1/entities")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}
