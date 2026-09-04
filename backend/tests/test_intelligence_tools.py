"""V3 §80: Intelligence tool routing tests (AT01-AT12).

覆盖：5 个只读 Tool 的 handler 输出、scope（§71）、unavailable 状态、
runtime case-scoped 注入（§70）、Coordinator/Expert allowlist（§73/§74）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.harness.agents import (
    _CRITIC_TOOLS,
    _OPINION_TOOLS,
    _PROPAGATION_TOOLS,
    _REPORT_TOOLS,
    _VALIDATOR_TOOLS,
    _VERIFICATION_TOOLS,
    build_coordinator_definition,
)
from app.harness.intelligence_tools import (
    IntelligenceToolReadService,
    register_intelligence_tools,
)
from app.harness.runtime import _CASE_SCOPED_TOOLS
from app.harness.tools import ToolRegistry

V3_TOOLS = frozenset(
    {
        "get_investigation_quality",
        "query_related_investigations",
        "query_workspace_entities",
        "get_workspace_entity",
        "query_signals",
    }
)


class _FakeQuality:
    async def evaluate(self, case_id: str, **_: Any) -> dict[str, Any]:
        return {
            "case_id": case_id,
            "overall_score": 72.5,
            "grade": "acceptable",
            "dimensions": [{"key": "collection", "label": "数据采集", "score": 80.0}],
            "gaps": [{"code": "g1", "severity": "warning", "message": "缺证据"}],
            "computed_at": "2026-09-01T00:00:00+00:00",
            "algorithm_version": "quality-1.0.0",
        }


class _FakeCross:
    async def related_investigations(self, case_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
        return [
            {
                "case_id": "case-b",
                "title": "调查B",
                "relation_types": ["shared_actor"],
                "relation_count": 1,
                "max_score": 0.8,
                "shared_actor_count": 1,
                "shared_post_count": 0,
                "shared_media_count": 0,
                "shared_content_count": 0,
                "has_candidate_relation": False,
            }
        ]


class _FakeWorkspace:
    async def list_case_entities(
        self, case_id: str, *, query=None, platform=None, limit=50, offset=0
    ) -> dict[str, Any]:
        return {
            "items": [
                {
                    "entity_id": "ent-1",
                    "canonical_name": "账号甲",
                    "platforms": ["weibo"],
                    "investigation_count": 2,
                    "post_count": 3,
                    "comment_count": 0,
                    "risk_summary": None,
                }
            ],
            "total": 1,
        }

    async def get_profile(self, entity_id: str) -> dict[str, Any]:
        return {
            "component_key": "ent-1",
            "entity_ids": ["ent-1"],
            "canonical_name": "账号甲",
            "platform_identities": [{"platform": "weibo", "native_id": "wb-1"}],
            "investigations": ["case-a", "case-b"],
            "recent_posts": [{"id": "p-1"}],
            "risk_summary": "band: high",
            "risk_assessments": [],
            "unresolved_local_risk": [],
            "coordination_memberships": [],
            "algorithm_version": "workspace-entity-1.0.0",
        }


class _FakeSignals:
    async def list_signals(self, **kwargs: Any) -> list[Any]:
        return [
            SimpleNamespace(
                id="sig-1",
                source_type="derived",
                source_label="Media reuse",
                signal_type="media_reuse",
                severity="warning",
                status="open",
                title="媒体复用",
                why_it_matters="跨调查复用",
                confidence=0.7,
                detector_active=True,
                detector_version="advanced-signal-1.0.0",
                related_case_ids=["case-a"],
                detected_at=None,
            )
        ]


def _make_service() -> IntelligenceToolReadService:
    async def _case_links(entity_id: str, case_id: str | None = None) -> list[Any]:
        return [SimpleNamespace(case_id="case-a")]

    async def _related_cases(case_id: str) -> list[str]:
        return ["case-b"]

    return IntelligenceToolReadService(
        quality_service=_FakeQuality(),
        cross_service=_FakeCross(),
        workspace_service=_FakeWorkspace(),
        signal_service=_FakeSignals(),
        workspace_repository=SimpleNamespace(list_case_links=_case_links),
        cross_repository=SimpleNamespace(related_case_ids=_related_cases),
    )


def _registry(service: IntelligenceToolReadService | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    register_intelligence_tools(registry, service)
    return registry


async def _call(registry: ToolRegistry, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    spec = registry.get(name)
    assert spec is not None, name
    return await spec.handler(dict(arguments))


# ---------------------------------------------------------------------------
# AT01-AT07: 工具 handler 行为
# ---------------------------------------------------------------------------


async def test_at01_get_investigation_quality_output() -> None:
    registry = _registry(_make_service())
    result = await _call(
        registry, "get_investigation_quality", {"case_id": "case-a"}
    )
    assert result["grade"] == "acceptable"
    assert result["overall_score"] == 72.5
    assert result["dimensions"][0]["key"] == "collection"
    assert result["top_gaps"][0]["severity"] == "warning"


async def test_at02_query_related_investigations_output_and_limit() -> None:
    registry = _registry(_make_service())
    result = await _call(
        registry,
        "query_related_investigations",
        {"case_id": "case-a", "limit": 50},
    )
    assert result["total"] == 1
    item = result["related_investigations"][0]
    assert item["case_id"] == "case-b"
    assert item["relation_types"] == ["shared_actor"]
    assert item["has_candidate_relation"] is False
    assert item["evidence_counts"]["shared_actor"] == 1


async def test_at03_query_related_filters_by_min_score() -> None:
    registry = _registry(_make_service())
    hit = await _call(
        registry,
        "query_related_investigations",
        {"case_id": "case-a", "min_score": 0.5},
    )
    assert hit["total"] == 1
    miss = await _call(
        registry,
        "query_related_investigations",
        {"case_id": "case-a", "min_score": 0.9},
    )
    assert miss["total"] == 0


async def test_at04_query_workspace_entities_case_scoped() -> None:
    registry = _registry(_make_service())
    result = await _call(
        registry, "query_workspace_entities", {"case_id": "case-a", "limit": 20}
    )
    assert result["total"] == 1
    assert result["items"][0]["entity_id"] == "ent-1"
    assert result["items"][0]["canonical_name"] == "账号甲"


async def test_at05_get_workspace_entity_scope_allowed() -> None:
    registry = _registry(_make_service())
    result = await _call(
        registry, "get_workspace_entity", {"case_id": "case-a", "entity_id": "ent-1"}
    )
    assert result["found"] is True
    assert result["component_key"] == "ent-1"
    assert result["case_appearances"] == ["case-a", "case-b"]
    assert len(result["recent_posts"]) <= 20


async def test_at06_get_workspace_entity_scope_denied() -> None:
    async def _no_case_links(entity_id: str, case_id: str | None = None) -> list[Any]:
        return []

    async def _no_related_cases(case_id: str) -> list[str]:
        return []

    denied_service = IntelligenceToolReadService(
        quality_service=_FakeQuality(),
        cross_service=_FakeCross(),
        workspace_service=_FakeWorkspace(),
        signal_service=_FakeSignals(),
        workspace_repository=SimpleNamespace(list_case_links=_no_case_links),
        cross_repository=SimpleNamespace(related_case_ids=_no_related_cases),
    )
    registry = _registry(denied_service)
    result = await _call(
        registry, "get_workspace_entity", {"case_id": "case-a", "entity_id": "ent-1"}
    )
    assert result["found"] is False


async def test_at07_query_signals_output_and_case_scope() -> None:
    registry = _registry(_make_service())
    result = await _call(
        registry, "query_signals", {"case_id": "case-a", "severity": "warning"}
    )
    assert result["total"] == 1
    signal = result["signals"][0]
    assert signal["signal_type"] == "media_reuse"
    assert signal["source_label"] == "Media reuse"
    assert signal["detector_active"] is True
    assert signal["related_case_ids"] == ["case-a"]


async def test_at08_unavailable_without_service() -> None:
    registry = _registry(None)
    result = await _call(
        registry, "query_signals", {"case_id": "case-a"}
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "intelligence_query_unavailable"


# ---------------------------------------------------------------------------
# AT09-AT12: 路由与 allowlist（§70/§73/§74）
# ---------------------------------------------------------------------------


def test_at09_all_tools_case_scoped_by_runtime() -> None:
    assert V3_TOOLS <= _CASE_SCOPED_TOOLS


def test_at10_coordinator_allowlist_includes_v3_tools() -> None:
    definition = build_coordinator_definition()
    assert V3_TOOLS <= definition.allowed_tools


def test_at11_expert_allowlists_follow_plan() -> None:
    # §74：Propagation 3 个、Verification 2 个、Critic/Report 各 quality
    assert {
        "query_related_investigations",
        "query_workspace_entities",
        "get_workspace_entity",
    } <= _PROPAGATION_TOOLS
    assert {"query_related_investigations", "get_workspace_entity"} <= _VERIFICATION_TOOLS
    assert "get_investigation_quality" in _CRITIC_TOOLS
    assert "get_investigation_quality" in _REPORT_TOOLS
    # Propagation/Verification 不得拥有 quality（保持按需授权）
    assert "get_investigation_quality" not in _PROPAGATION_TOOLS
    assert "get_investigation_quality" not in _VERIFICATION_TOOLS


def test_at12_opinion_and_validator_get_no_v3_tools() -> None:
    # §74：Opinion / Citation Validator 不新增 V3 Tool
    assert not (V3_TOOLS & _OPINION_TOOLS)
    assert not (V3_TOOLS & _VALIDATOR_TOOLS)
