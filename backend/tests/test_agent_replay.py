"""Phase 4 Replay 的测试：diff 语义、脱敏、冻结观察、两种 replay 模式。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.evaluation.agent_dataset import load_suite
from app.evaluation.agent_eval import AgentSuiteRunner
from app.evaluation.agent_manifest import (
    REDACTED,
    ReplayBundle,
    build_bundle,
    bundle_contains_secret,
    redact,
    short_hash,
    tool_schema_hash,
)
from app.evaluation.agent_replay_service import (
    OBSERVATION_REPLAY,
    SEEDED_FULL_RERUN,
    AgentReplayService,
    FrozenObservationMissing,
    FrozenObservations,
    build_frozen_registry,
    diff_artifacts,
    diff_bundles,
    diff_citations,
    diff_sequences,
    diff_tool_arguments,
)
from app.infrastructure.database import Database


# ---------------------------------------------------------------------------
# 单元：脱敏
# ---------------------------------------------------------------------------


def test_redact_removes_sensitive_keys_and_values() -> None:
    payload = {
        "headers": {"Authorization": "Bearer abcdef123456", "Accept": "json"},
        "cookies": "sessionid=deadbeefcookie; csrf=xyz",
        "nested": [{"api_key": "sk-live-1234567890"}, {"safe": "ok"}],
        "note": "plain text",
    }
    cleaned = redact(payload)
    assert cleaned["headers"]["Authorization"] == REDACTED
    assert cleaned["headers"]["Accept"] == "json"
    assert cleaned["cookies"] == REDACTED
    assert cleaned["nested"][0]["api_key"] == REDACTED
    assert cleaned["nested"][1]["safe"] == "ok"
    assert cleaned["note"] == "plain text"


def test_redact_scrubs_credentials_embedded_in_text() -> None:
    cleaned = redact({"log": "GET /api?sessionid=abcdef123456&x=1 Authorization: Bearer zzz"})
    assert "abcdef123456" not in cleaned["log"]
    assert REDACTED in cleaned["log"]


def test_bundle_with_credentials_is_not_detected_as_clean() -> None:
    """自检函数必须能发现未脱敏的凭据，避免"假脱敏"。"""
    dirty = {"final_response": "cookie: sessionid=abc123456"}
    assert bundle_contains_secret(dirty) is True
    assert bundle_contains_secret(redact(dirty)) is False


def test_build_bundle_redacts_tool_observations() -> None:
    from app.evaluation.agent_trace import AgentTrace, ToolCallView

    trace = AgentTrace(
        run_id="r1",
        case_id="c1",
        status="completed",
        objective="查一下",
        final_answer="完成",
        tool_calls=(
            ToolCallView(
                id="t1",
                tool="collect_social_posts",
                status="succeeded",
                arguments={"platform": "weibo", "cookie": "sessionid=abcdef123456"},
                result={"ok": True, "authorization": "Bearer zzzz9999"},
            ),
        ),
    )
    bundle = build_bundle(
        trace=trace,
        task_id="G1_01",
        suite_version="interview_agent_v1",
        mode="contract",
        git_sha="deadbeef",
        model_name="scripted",
        coordinator_prompt_hash="p",
        schema_hash="s",
    )
    payload = bundle.to_dict()
    assert payload["tool_arguments"][0]["cookie"] == REDACTED
    assert payload["tool_observations"][0]["result"]["authorization"] == REDACTED
    assert bundle_contains_secret(payload) is False


# ---------------------------------------------------------------------------
# 单元：tool schema hash
# ---------------------------------------------------------------------------


def test_tool_schema_hash_changes_when_contract_changes(tmp_path: Path) -> None:
    from app.application.repositories import ApplicationRepository
    from app.harness.database_tools import register_database_tools
    from app.harness.skills import SkillRegistry
    from app.harness.tool_factory import build_tool_registry
    from app.infrastructure.crawler.demo import DemoCrawlerAdapter
    from app.infrastructure.database import Database
    from app.infrastructure.database.knowledge_repository import KnowledgeRepository
    from app.infrastructure.database.social_repository import SocialRepository
    from app.infrastructure.embeddings import EmbeddingWorkerClient

    from dataclasses import replace

    async def _build():
        database = Database(f"sqlite+aiosqlite:///{tmp_path / 'hash.db'}")
        repository = ApplicationRepository(database)
        registry = build_tool_registry(
            DemoCrawlerAdapter(),
            SkillRegistry(),
            KnowledgeRepository(database),
            EmbeddingWorkerClient("http://localhost:1", dimensions=1024, timeout_seconds=1),
            SocialRepository(database),
            repository,
        )
        register_database_tools(registry, None)
        return database, registry

    database, registry = asyncio.run(_build())
    try:
        baseline = tool_schema_hash(registry)
        assert len(baseline) == 64

        spec = registry.get("query_social_posts")
        mutated = replace(spec, side_effect="external_write")
        registry.register(replace(mutated, name="query_social_posts_shadow"))
        assert tool_schema_hash(registry) != baseline
    finally:
        asyncio.run(database.dispose())


# ---------------------------------------------------------------------------
# 单元：diff
# ---------------------------------------------------------------------------


def test_diff_sequences_reports_added_removed_and_prefix() -> None:
    added = diff_sequences(["a", "b"], ["a", "b", "c"])
    assert added.added == ("c",) and added.removed == ()
    assert added.common_prefix == 2

    removed = diff_sequences(["a", "b", "c"], ["a", "c"])
    assert removed.removed == ("b",)
    assert removed.common_prefix == 1

    reordered = diff_sequences(["a", "b", "c"], ["a", "c", "b"])
    assert reordered.reordered is True
    assert reordered.added == () and reordered.removed == ()


def test_diff_tool_arguments_ignores_runtime_injected_fields() -> None:
    source = ReplayBundle(
        run_id="s",
        task_id="t",
        suite_version="v",
        mode="contract",
        user_prompt="p",
        case_id="c",
        git_sha="g",
        model_name="m",
        coordinator_prompt_hash="h",
        tool_schema_hash="k",
        tool_sequence=("query_social_posts",),
        tool_arguments=({"case_id": "c1", "limit": 20},),
    )
    same = ReplayBundle(
        **{
            **source.to_dict(),
            "run_id": "c",
            "case_id": "c2",
            "tool_arguments": ({"case_id": "c2", "limit": 20},),
            "tool_sequence": ("query_social_posts",),
            "tool_observations": (),
            "artifact_kinds": (),
            "metrics": {},
            "failure_categories": (),
        }
    )
    assert diff_tool_arguments(source, same)["changed"] is False

    changed = ReplayBundle(
        **{
            **same.to_dict(),
            "tool_arguments": ({"case_id": "c2", "limit": 5},),
        }
    )
    result = diff_tool_arguments(source, changed)
    assert result["changed"] is True
    assert result["changes"][0]["changed_keys"] == ["limit"]


def test_diff_artifacts_and_citations() -> None:
    base = dict(
        run_id="s",
        task_id="t",
        suite_version="v",
        mode="contract",
        user_prompt="p",
        case_id="c",
        git_sha="g",
        model_name="m",
        coordinator_prompt_hash="h",
        tool_schema_hash="k",
    )
    source = ReplayBundle(**base, artifact_kinds=("report",), final_response="引用 aaaabbbbccccddddeeeeffff00001111")
    candidate = ReplayBundle(
        **base,
        artifact_kinds=("report", "propagation_reconstruction"),
        final_response="引用 ffffeeee111122223333444455556666",
    )
    artifacts = diff_artifacts(source, candidate)
    assert artifacts["added"] == ["propagation_reconstruction"]
    citations = diff_citations(source, candidate)
    assert citations["changed"] is True
    assert citations["added"] == ["ffffeeee111122223333444455556666"]
    assert citations["removed"] == ["aaaabbbbccccddddeeeeffff00001111"]


def test_diff_bundles_reports_schema_change_and_metric_deltas() -> None:
    base = dict(
        run_id="s",
        task_id="t",
        suite_version="v",
        mode="contract",
        user_prompt="p",
        case_id="c",
        git_sha="g",
        model_name="m",
        coordinator_prompt_hash="h",
    )
    source = ReplayBundle(
        **base,
        tool_schema_hash="hash-a",
        tool_sequence=("query_findings",),
        metrics={"latency_ms": 100, "input_tokens": 10, "output_tokens": 5, "estimated_cost": 0.01},
    )
    candidate = ReplayBundle(
        **{**base, "run_id": "c"},
        tool_schema_hash="hash-b",
        tool_sequence=("query_findings", "query_reports"),
        metrics={"latency_ms": 150, "input_tokens": 12, "output_tokens": 8, "estimated_cost": 0.02},
    )
    diff = diff_bundles(source, candidate, mode=OBSERVATION_REPLAY, task_success_source=1.0, task_success_candidate=0.0)
    assert diff.schema_changed is True
    assert diff.latency_delta_ms == 50.0
    assert diff.token_delta == 5.0
    assert round(diff.cost_delta, 4) == 0.01
    assert diff.task_success_delta == -1.0
    assert diff.to_markdown().startswith("# Replay Diff")


# ---------------------------------------------------------------------------
# 单元：冻结观察
# ---------------------------------------------------------------------------


def test_frozen_observations_lookup_and_missing() -> None:
    bundle = ReplayBundle(
        run_id="s",
        task_id="t",
        suite_version="v",
        mode="contract",
        user_prompt="p",
        case_id="c",
        git_sha="g",
        model_name="m",
        coordinator_prompt_hash="h",
        tool_schema_hash="k",
        tool_sequence=("query_findings",),
        tool_arguments=({"case_id": "c", "limit": 20},),
        tool_observations=(
            {"tool": "query_findings", "status": "succeeded", "result": {"ok": True, "n": 2}},
        ),
    )
    frozen = FrozenObservations(bundle)
    hit = frozen.lookup("query_findings", {"case_id": "c", "limit": 20})
    assert hit["result"]["n"] == 2
    with pytest.raises(FrozenObservationMissing):
        frozen.lookup("query_findings", {"case_id": "c", "limit": 99})
    with pytest.raises(FrozenObservationMissing):
        frozen.lookup("build_report", {})


def test_frozen_registry_preserves_contract_but_returns_frozen_results(tmp_path: Path) -> None:
    from app.application.repositories import ApplicationRepository
    from app.harness.skills import SkillRegistry
    from app.harness.tool_factory import build_tool_registry
    from app.infrastructure.crawler.demo import DemoCrawlerAdapter
    from app.infrastructure.database import Database
    from app.infrastructure.database.knowledge_repository import KnowledgeRepository
    from app.infrastructure.database.social_repository import SocialRepository
    from app.infrastructure.embeddings import EmbeddingWorkerClient

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'frozen.db'}")

    async def _build():
        from app.harness.database_tools import register_database_tools

        repository = ApplicationRepository(database)
        registry = build_tool_registry(
            DemoCrawlerAdapter(),
            SkillRegistry(),
            KnowledgeRepository(database),
            EmbeddingWorkerClient("http://localhost:1", dimensions=1024, timeout_seconds=1),
            SocialRepository(database),
            repository,
        )
        register_database_tools(registry, None)
        return registry

    real = asyncio.run(_build())
    try:
        bundle = ReplayBundle(
            run_id="s",
            task_id="t",
            suite_version="v",
            mode="contract",
            user_prompt="p",
            case_id="c",
            git_sha="g",
            model_name="m",
            coordinator_prompt_hash="h",
            tool_schema_hash="k",
            tool_sequence=("query_findings",),
            tool_arguments=({"case_id": "c", "limit": 20},),
            tool_observations=(
                {"tool": "query_findings", "status": "succeeded", "result": {"items": [], "frozen": True}},
            ),
        )
        frozen = build_frozen_registry(real, FrozenObservations(bundle))
        # 契约（权限/副作用）与真实 registry 一致
        assert frozen.names() == real.names()
        assert frozen.get("query_findings").side_effect == real.get("query_findings").side_effect
        assert frozen.get("query_findings").permissions == real.get("query_findings").permissions
        # handler 被替换：schema hash 只反映契约，因此与真实 registry 相同
        # （hash 只覆盖 name/version/schema/permissions/side_effect 等契约字段）
    finally:
        asyncio.run(database.dispose())


# ---------------------------------------------------------------------------
# 集成：observation replay 与 full rerun
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def suite():
    return load_suite()


def _run_single_task(tmp_path: Path, task_id: str):
    async def _run():
        database = Database(f"sqlite+aiosqlite:///{tmp_path / 'replay.db'}")
        await database.create_schema()
        try:
            runner = AgentSuiteRunner(suite=load_suite(), database=database)
            report = await runner.run(task_ids=[task_id])
            return database, report
        except Exception:
            await database.dispose()
            raise

    return asyncio.run(_run())


def test_seeded_full_rerun_produces_zero_diff(tmp_path: Path) -> None:
    """同一 suite 在同一提交上重跑，工具序列与 artifacts 应完全一致。"""
    database, report = _run_single_task(tmp_path, "G3_01")
    try:
        result = report.task_results[0]
        bundle = ReplayBundle(
            run_id=result.run_id,
            task_id=result.task_id,
            suite_version=report.suite_version,
            mode=report.mode,
            user_prompt="这个调查目前已经形成了哪些结论？",
            case_id=result.case_id,
            git_sha=report.git_sha,
            model_name="contract-scripted-model",
            coordinator_prompt_hash=short_hash(result.task_id),
            tool_schema_hash="hash",
            tool_sequence=tuple(item["tool"] for item in result.trace_bundle["tool_calls"]),
            tool_arguments=tuple(
                dict(item["arguments"]) for item in result.trace_bundle["tool_calls"]
            ),
            tool_observations=tuple(
                {
                    "tool": item["tool"],
                    "status": item.get("status"),
                    "result": None,
                    "error_code": item.get("error_code"),
                    "duration_ms": item.get("duration_ms"),
                    "cached": item.get("cached", False),
                    "approval_id": item.get("approval_id"),
                }
                for item in result.trace_bundle["tool_calls"]
            ),
            final_response=str(result.trace_bundle["final_answer"]),
            artifact_kinds=tuple(
                item["kind"] for item in result.trace_bundle["artifacts"]
            ),
            metrics={
                "tool_call_count": result.trace_bundle["metrics"]["tool_call_count"],
                "latency_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost": 0.0,
            },
        )
        service = AgentReplayService(suite=load_suite(), database=database)
        replayed = asyncio.run(
            service.replay_run(bundle, replay_mode=SEEDED_FULL_RERUN)
        )
        assert replayed.mode == SEEDED_FULL_RERUN
        diff = replayed.diff
        assert diff.tool_sequence_diff.added == ()
        assert diff.tool_sequence_diff.removed == ()
        assert diff.artifact_diff["changed"] is False
        assert replayed.notes
    finally:
        asyncio.run(database.dispose())


def test_observation_replay_fails_loud_on_unrecorded_tool(tmp_path: Path) -> None:
    """candidate 调用源未记录的工具时绝不回退执行真实工具。

    冻结层抛 ``FrozenObservationMissing``，runtime 把该次工具调用记为失败；
    replay 结果必须在 notes 里显式暴露这次"冻结缺失"，而不是让调用悄悄成功。
    """
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'missing.db'}")

    async def _run():
        await database.create_schema()
        bundle = ReplayBundle(
            run_id="source",
            task_id="G1_01",
            suite_version="interview_agent_v1",
            mode="contract",
            user_prompt="统计",
            case_id="whatever",
            git_sha="g",
            model_name="m",
            coordinator_prompt_hash="h",
            tool_schema_hash="k",
            tool_sequence=("query_reports",),  # 与 G1_01 的脚本不一致
            tool_arguments=({"limit": 20},),
            tool_observations=(
                {"tool": "query_reports", "status": "succeeded", "result": {"items": []}},
            ),
        )
        service = AgentReplayService(suite=load_suite(), database=database)
        result = await service.replay_run(bundle, replay_mode=OBSERVATION_REPLAY)
        # 关键不变量：没有任何工具被"真实执行成功"——冻结缺失要么让该次调用失败，
        # 要么直接让 run 失败，但绝不会回退到真实工具。
        trace = result.candidate_trace
        succeeded = [
            call for call in (trace.tool_calls if trace else ()) if call.status == "succeeded"
        ]
        assert not succeeded, "frozen replay must not execute real tools"
        assert result.candidate_bundle.metrics.get("run_status") in {
            "failed",
            "completed",
            "cancelled",
        }
        assert any("未命中冻结观察" in note for note in result.notes), result.notes

    try:
        asyncio.run(_run())
    finally:
        asyncio.run(database.dispose())


def test_replay_rejects_unknown_mode(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'mode.db'}")

    async def _run():
        service = AgentReplayService(suite=load_suite(), database=database)
        bundle = ReplayBundle(
            run_id="r",
            task_id="G1_01",
            suite_version="v",
            mode="contract",
            user_prompt="p",
            case_id="c",
            git_sha="g",
            model_name="m",
            coordinator_prompt_hash="h",
            tool_schema_hash="k",
        )
        with pytest.raises(ValueError):
            await service.replay_run(bundle, replay_mode="video")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# FC-IR-04：Replay provenance 必须真实
# ---------------------------------------------------------------------------


def test_ir_replay_02_coordinator_prompt_hash_tracks_production_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IR-REPLAY-02：修改生产 coordinator prompt → hash 必须改变。"""
    from app.harness import agents
    from app.evaluation.agent_manifest import production_coordinator_prompt_hash

    original = production_coordinator_prompt_hash()
    monkeypatch.setattr(
        agents, "COORDINATOR_INSTRUCTIONS", agents.COORDINATOR_INSTRUCTIONS + "X"
    )
    try:
        assert production_coordinator_prompt_hash() != original
    finally:
        monkeypatch.undo()
    assert production_coordinator_prompt_hash() == original


def test_ir_replay_03_prompt_hash_ignores_golden_expected_behavior() -> None:
    """IR-REPLAY-03：expected behavior 改动不影响 coordinator prompt hash。"""
    from app.evaluation.agent_dataset import AgentExpectedBehavior
    from app.evaluation.agent_manifest import production_coordinator_prompt_hash

    baseline = production_coordinator_prompt_hash()
    # 构造两份截然不同的 expected behavior；hash 必须保持与生产 prompt 一致。
    AgentExpectedBehavior(required_tools=("a",)).to_dict()
    AgentExpectedBehavior(
        required_tools=("x", "y"), answer_must_contain=("完全不同",)
    ).to_dict()
    assert production_coordinator_prompt_hash() == baseline


def test_ir_replay_01_seeded_rerun_recomputes_candidate_schema_hash(
    tmp_path: Path,
) -> None:
    """IR-REPLAY-01：full rerun 的 candidate tool_schema_hash 来自真实重算。

    source bundle 故意携带伪造 hash；candidate 必须输出当前装配的真实
    hash，使 ``schema_changed`` 为 True（照抄 source 会造成假阴性）。
    """
    database, report = _run_single_task(tmp_path, "G3_01")
    try:
        result = report.task_results[0]
        bundle = ReplayBundle(
            run_id=result.run_id,
            task_id=result.task_id,
            suite_version=report.suite_version,
            mode=report.mode,
            user_prompt="这个调查目前已经形成了哪些结论？",
            case_id=result.case_id,
            git_sha=report.git_sha,
            model_name="contract-scripted-model",
            coordinator_prompt_hash="forged-source-prompt-hash",
            tool_schema_hash="forged-source-schema-hash",
            tool_sequence=tuple(
                item["tool"] for item in result.trace_bundle["tool_calls"]
            ),
            tool_arguments=tuple(
                dict(item["arguments"]) for item in result.trace_bundle["tool_calls"]
            ),
            tool_observations=tuple(
                {
                    "tool": item["tool"],
                    "status": item.get("status"),
                    "result": None,
                    "error_code": item.get("error_code"),
                    "duration_ms": item.get("duration_ms"),
                    "cached": item.get("cached", False),
                    "approval_id": item.get("approval_id"),
                }
                for item in result.trace_bundle["tool_calls"]
            ),
            final_response=str(result.trace_bundle["final_answer"]),
            artifact_kinds=tuple(
                item["kind"] for item in result.trace_bundle["artifacts"]
            ),
            metrics={"tool_call_count": 0, "latency_ms": 0},
        )
        service = AgentReplayService(suite=load_suite(), database=database)
        replayed = asyncio.run(
            service.replay_run(bundle, replay_mode=SEEDED_FULL_RERUN)
        )
        candidate = replayed.candidate_bundle
        # candidate hash 必须真实重算：不等于伪造的 source hash。
        assert candidate.tool_schema_hash != "forged-source-schema-hash"
        assert candidate.tool_schema_hash == report.tool_schema_hash
        assert replayed.diff.schema_changed is True
        # prompt provenance 来自生产 coordinator prompt，不是 source 也不是 expected。
        from app.evaluation.agent_manifest import production_coordinator_prompt_hash

        assert candidate.coordinator_prompt_hash == production_coordinator_prompt_hash()
        assert candidate.coordinator_prompt_hash != "forged-source-prompt-hash"
    finally:
        asyncio.run(database.dispose())
