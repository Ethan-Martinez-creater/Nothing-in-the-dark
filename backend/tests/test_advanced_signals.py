"""V3 §78: Advanced Signals tests (S01-S22).

detector 依赖（workspace/media/cross/integrity/jobs）用内存 stub 注入
（async mock），DerivedSignalRepository + SignalService 用真实内存 SQLite
（tests/memory_db.py）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.application.advanced_signal_service import (
    AdvancedSignalDetectorService,
    _fingerprint,
)
from app.application.signal_service import SignalService
from app.core.errors import ResourceNotFoundError
from app.infrastructure.database.derived_signal_repository import (
    DerivedSignalRepository,
)
from tests.memory_db import MemoryDatabase

SIGNAL_KWARGS: dict[str, Any] = {
    "source_type": "derived",
    "source_id": "subj-1",
    "signal_type": "actor_recurrence",
    "severity": "warning",
    "title": "该主体在多个 Investigation 中重复出现",
    "why_it_matters": "重复出现",
    "confidence": None,
    "metric_snapshot": {},
    "evidence_refs": [],
    "related_case_ids": ["case-a"],
    "detector_version": "advanced-signal-1.0.0",
}


def _async_return(value: Any) -> Any:
    async def _inner(*args: Any, **kwargs: Any) -> Any:
        return value

    return _inner


async def _setup() -> SimpleNamespace:
    database = MemoryDatabase()
    await database.create_schema()
    derived = DerivedSignalRepository(database)
    return SimpleNamespace(db=database, derived=derived)


def _payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        **SIGNAL_KWARGS,
        "fingerprint": f"fp-{overrides.get('source_id', 'subj-1')}",
    }
    payload.update(overrides)
    payload.setdefault("case_id", "case-a")
    return payload


def _make_detector(
    env: SimpleNamespace,
    *,
    workspace: Any = None,
    media: Any = None,
    cross: Any = None,
    integrity: Any = None,
    jobs: Any = None,
    app_repo: Any = None,
) -> AdvancedSignalDetectorService:
    return AdvancedSignalDetectorService(
        derived_repository=env.derived,
        integrity_repository=integrity,
        analysis_job_repository=jobs,
        workspace_service=workspace,
        cross_repository=cross,
        media_repository=media,
        application_repository=app_repo,
    )


def _empty_cases() -> SimpleNamespace:
    return SimpleNamespace(list_cases=_async_return([]))


# ---------------------------------------------------------------------------
# S01-S04: Derived Signal lifecycle（§11.2）
# ---------------------------------------------------------------------------


async def test_s01_create_open_occurrence_one() -> None:
    env = await _setup()
    record = await env.derived.upsert_observed_signal(
        **_payload(source_id="a", fingerprint="fp-a")
    )
    assert record.status == "open"
    assert record.detector_active is True
    assert record.occurrence_count == 1
    assert record.first_seen_at is not None
    links = await env.derived.list_case_links(record.id)
    assert links == ["case-a"]
    await env.db.dispose()


async def test_s02_true_to_true_keeps_status_and_occurrence() -> None:
    env = await _setup()
    first = await env.derived.upsert_observed_signal(
        **_payload(source_id="a", fingerprint="fp-a")
    )
    await env.derived.set_status(first.id, "acknowledged")
    second = await env.derived.upsert_observed_signal(
        **_payload(source_id="a", fingerprint="fp-a", metric_snapshot={"n": 2})
    )
    assert second.id == first.id
    assert second.status == "acknowledged"  # 不改变 acknowledged
    assert second.occurrence_count == 1  # 不增加
    assert second.detector_active is True
    assert second.metric_snapshot_json == {"n": 2}  # 更新 snapshot
    await env.db.dispose()


async def test_s03_false_to_true_reopens_and_increments() -> None:
    env = await _setup()
    record = await env.derived.upsert_observed_signal(
        **_payload(source_id="a", fingerprint="fp-a")
    )
    await env.derived.reconcile_detector_scope(
        signal_type="actor_recurrence",
        detector_version="advanced-signal-1.0.0",
        case_ids=["case-a"],
        expected_fingerprints=[],
    )
    reloaded = await env.derived.get(record.id)
    assert reloaded.detector_active is False
    assert reloaded.status == "resolved"  # open → 自动 resolved

    revived = await env.derived.upsert_observed_signal(
        **_payload(source_id="a", fingerprint="fp-a")
    )
    assert revived.detector_active is True
    assert revived.status == "open"  # 重新出现 → open
    assert revived.occurrence_count == 2  # +1
    await env.db.dispose()


async def test_s04_suppressed_stays_suppressed_on_reopen() -> None:
    env = await _setup()
    record = await env.derived.upsert_observed_signal(
        **_payload(source_id="a", fingerprint="fp-a")
    )
    await env.derived.set_status(record.id, "suppressed")
    await env.derived.reconcile_detector_scope(
        signal_type="actor_recurrence",
        detector_version="advanced-signal-1.0.0",
        case_ids=["case-a"],
        expected_fingerprints=[],
    )
    deactivated = await env.derived.get(record.id)
    assert deactivated.detector_active is False
    assert deactivated.status == "suppressed"  # 条件消失保持 suppressed

    revived = await env.derived.upsert_observed_signal(
        **_payload(source_id="a", fingerprint="fp-a")
    )
    assert revived.status == "suppressed"  # 重现保持 suppressed
    assert revived.occurrence_count == 2  # 只更新 occurrence/evidence
    await env.db.dispose()


# ---------------------------------------------------------------------------
# S05-S07: coordination_cluster（§52/§52.1）
# ---------------------------------------------------------------------------


def _coordination_env(env: SimpleNamespace, *, size: int, score: float) -> SimpleNamespace:
    integrity = SimpleNamespace(
        get_cluster=_async_return(
            SimpleNamespace(
                id="cluster-1",
                case_id="case-a",
                size=size,
                score=score,
                members=[{"account_id": f"acc-{i}"} for i in range(size)],
            )
        )
    )
    jobs = SimpleNamespace(
        latest_succeeded=_async_return(
            SimpleNamespace(result_json={"cluster_ids": ["cluster-1"]})
        )
    )
    return _make_detector(env, integrity=integrity, jobs=jobs)


async def test_s05_coordination_threshold_and_severity() -> None:
    env = await _setup()
    detector = _coordination_env(env, size=4, score=0.80)
    summary = await detector.refresh_coordination(["case-a"])
    assert summary["upserted"] == 1

    signals = await env.derived.list()
    assert len(signals) == 1
    signal = signals[0]
    assert signal.signal_type == "coordination_cluster"
    assert signal.severity == "warning"  # score 0.80 < 0.90 → warning
    assert signal.case_id == "case-a"
    links = await env.derived.list_case_links(signal.id)
    assert links == ["case-a"]
    assert "疑似协调" in signal.title
    await env.db.dispose()


async def test_s06_coordination_critical_when_score_and_size_high() -> None:
    env = await _setup()
    detector = _coordination_env(env, size=6, score=0.95)
    await detector.refresh_coordination(["case-a"])
    signals = await env.derived.list()
    assert signals[0].severity == "critical"  # score 0.95 >= 0.90 AND size 6 >= 5
    await env.db.dispose()


async def test_s07_coordination_below_threshold_no_signal() -> None:
    env = await _setup()
    detector = _coordination_env(env, size=2, score=0.99)
    summary = await detector.refresh_coordination(["case-a"])
    assert summary["upserted"] == 0  # size 2 < 3
    assert await env.derived.list() == []
    await env.db.dispose()


# ---------------------------------------------------------------------------
# S08-S10: actor_recurrence（§53）
# ---------------------------------------------------------------------------


def _actor_env(env: SimpleNamespace, cases: list[str]) -> SimpleNamespace:
    workspace = SimpleNamespace(
        list_components_with_cases=_async_return(
            [{"component_key": "ent-1", "entity_ids": ["ent-1"], "cases": cases}]
        )
    )
    return _make_detector(env, workspace=workspace)


async def test_s08_actor_recurrence_warning_at_three_cases() -> None:
    env = await _setup()
    detector = _actor_env(env, ["case-a", "case-b", "case-c"])
    summary = await detector.refresh_actor_recurrence()
    assert summary["upserted"] == 1
    signal = (await env.derived.list())[0]
    assert signal.severity == "warning"  # 3-4 cases
    assert signal.case_id == "case-a"  # 字典序最小
    assert sorted(signal.related_case_ids_json) == ["case-a", "case-b", "case-c"]
    assert "重复出现" in signal.title
    await env.db.dispose()


async def test_s09_actor_recurrence_critical_at_five_cases() -> None:
    env = await _setup()
    detector = _actor_env(env, ["case-a", "case-b", "case-c", "case-d", "case-e"])
    await detector.refresh_actor_recurrence()
    signal = (await env.derived.list())[0]
    assert signal.severity == "critical"
    await env.db.dispose()


async def test_s10_actor_recurrence_below_three_cases_no_signal() -> None:
    env = await _setup()
    detector = _actor_env(env, ["case-a", "case-b"])
    summary = await detector.refresh_actor_recurrence()
    assert summary["upserted"] == 0
    await env.db.dispose()


# ---------------------------------------------------------------------------
# S11-S13: media_reuse（§54）
# ---------------------------------------------------------------------------


def _media_env(env: SimpleNamespace, rows: list[dict[str, Any]]) -> SimpleNamespace:
    media = SimpleNamespace(list_sha_case_counts=_async_return(rows))
    return _make_detector(env, media=media)


async def test_s11_media_reuse_exact_sha_warning() -> None:
    env = await _setup()
    detector = _media_env(
        env,
        [
            {
                "sha256": "sha-1",
                "case_count": 2,
                "case_ids": ["case-a", "case-b"],
            }
        ],
    )
    summary = await detector.refresh_media_reuse()
    assert summary["upserted"] == 1
    signal = (await env.derived.list())[0]
    assert signal.severity == "warning"  # 2-3 cases
    assert signal.source_id == "sha-1"
    assert sorted(signal.related_case_ids_json) == ["case-a", "case-b"]
    assert signal.case_id == "case-a"  # 字典序最小
    await env.db.dispose()


async def test_s12_media_reuse_critical_at_four_cases() -> None:
    env = await _setup()
    detector = _media_env(
        env,
        [
            {
                "sha256": "sha-2",
                "case_count": 4,
                "case_ids": ["case-a", "case-b", "case-c", "case-d"],
            }
        ],
    )
    await detector.refresh_media_reuse()
    signal = (await env.derived.list())[0]
    assert signal.severity == "critical"
    await env.db.dispose()


async def test_s13_media_reuse_single_case_no_signal() -> None:
    env = await _setup()
    detector = _media_env(
        env, [{"sha256": "sha-1", "case_count": 1, "case_ids": ["case-a"]}]
    )
    summary = await detector.refresh_media_reuse()
    assert summary["upserted"] == 0
    await env.db.dispose()


# ---------------------------------------------------------------------------
# S14-S16: cross_case_overlap（§55）
# ---------------------------------------------------------------------------


def _cross_link(left: str, right: str, etype: str, count: int, score: float = 1.0):
    """真实 Cross Link contract（Rework R2）：relation_type 用 shared_* 命名，
    贡献量来自 link.evidence_count（不从 evidence_refs 推断）。"""
    return SimpleNamespace(
        left_case_id=left,
        right_case_id=right,
        relation_type=etype,
        is_active=True,
        status="observed",
        score=score,
        evidence_count=count,
        evidence_refs_json=[{"type": etype}] * count,
    )


def _overlap_env(env: SimpleNamespace, links: list[Any]) -> SimpleNamespace:
    cross = SimpleNamespace(list_workspace=_async_return(links))
    return _make_detector(env, cross=cross)


async def test_s14_overlap_formula_and_warning() -> None:
    env = await _setup()
    # shared_actor 3（→1.0）+ shared_media 2（→1.0）+ content 0 + post 0
    # score = 0.40 + 0.30 = 0.70 >= 0.60，2 relation types → warning
    detector = _overlap_env(
        env,
        [
            _cross_link("case-a", "case-b", "shared_actor", 3),
            _cross_link("case-a", "case-b", "shared_media", 2),
        ],
    )
    summary = await detector.refresh_cross_case_overlap()
    assert summary["upserted"] == 1
    signal = (await env.derived.list())[0]
    assert signal.severity == "warning"
    assert signal.metric_snapshot_json["overlap_score"] == 0.70
    assert sorted(signal.related_case_ids_json) == ["case-a", "case-b"]
    await env.db.dispose()


async def test_s15_overlap_critical_at_high_score() -> None:
    env = await _setup()
    # shared_actor 3 + shared_media 2 + shared_content 5（→1.0）
    # score = 0.40 + 0.30 + 0.20 = 0.90 >= 0.85 → critical
    detector = _overlap_env(
        env,
        [
            _cross_link("case-a", "case-b", "shared_actor", 3),
            _cross_link("case-a", "case-b", "shared_media", 2),
            _cross_link("case-a", "case-b", "shared_content", 5),
        ],
    )
    await detector.refresh_cross_case_overlap()
    signal = (await env.derived.list())[0]
    assert signal.severity == "critical"
    await env.db.dispose()


async def test_s16_overlap_single_relation_type_no_signal() -> None:
    env = await _setup()
    detector = _overlap_env(
        env, [_cross_link("case-a", "case-b", "shared_actor", 10)]
    )
    summary = await detector.refresh_cross_case_overlap()
    # 单 relation type 不满足 >=2 类型，即使 actor 特征封顶 1.0 也不触发
    assert summary["upserted"] == 0
    await env.db.dispose()


# ---------------------------------------------------------------------------
# S17-S19: Detector reconciliation（§56）
# ---------------------------------------------------------------------------


async def test_s17_reconcile_deactivates_stale_actor_signal() -> None:
    env = await _setup()
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="ent-1",
            fingerprint=_fingerprint("actor_recurrence", "ent-1", "advanced-signal-1.0.0"),
        )
    )
    workspace = SimpleNamespace(
        list_components_with_cases=_async_return(
            [
                {
                    "component_key": "ent-1",
                    "entity_ids": ["ent-1"],
                    "cases": ["case-a", "case-b", "case-c"],
                }
            ]
        )
    )
    detector = _make_detector(env, workspace=workspace)
    await detector.refresh_actor_recurrence()
    record = await env.derived.list()
    assert len(record) == 1
    assert record[0].detector_active is True  # 仍成立

    # 组件降到 2 个 Case → 不再满足 → detector_active=false + resolved
    workspace.list_components_with_cases = _async_return(
        [
            {
                "component_key": "ent-1",
                "entity_ids": ["ent-1"],
                "cases": ["case-a", "case-b"],
            }
        ]
    )
    summary = await detector.refresh_actor_recurrence()
    assert summary["stale_deactivated"] == 1
    record = (await env.derived.list())[0]
    assert record.detector_active is False
    assert record.status == "resolved"
    await env.db.dispose()


async def test_s18_reconcile_scoped_to_detector_and_case() -> None:
    env = await _setup()
    # 同 type 不同 case 的 signal 不受其他 case reconcile 影响
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="ent-1",
            fingerprint="fp-1",
            related_case_ids=["case-a"],
        )
    )
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="ent-2",
            fingerprint="fp-2",
            case_id="case-b",
            related_case_ids=["case-b"],
        )
    )
    deactivated = await env.derived.reconcile_detector_scope(
        signal_type="actor_recurrence",
        detector_version="advanced-signal-1.0.0",
        case_ids=["case-a"],
        expected_fingerprints=["fp-1"],
    )
    assert deactivated == 0  # fp-1 在 expected 中；fp-2 的 case link 不在 scope
    # 移除 fp-1 → case-a scope 内被 deactivate，case-b 不受影响
    deactivated = await env.derived.reconcile_detector_scope(
        signal_type="actor_recurrence",
        detector_version="advanced-signal-1.0.0",
        case_ids=["case-a"],
        expected_fingerprints=[],
    )
    assert deactivated == 1
    records = await env.derived.list()
    by_fp = {record.fingerprint: record for record in records}
    assert by_fp["fp-1"].detector_active is False
    assert by_fp["fp-2"].detector_active is True
    await env.db.dispose()


async def test_s19_reconcile_does_not_touch_other_detector() -> None:
    env = await _setup()
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="m-1",
            fingerprint="fp-media",
            signal_type="media_reuse",
            related_case_ids=["case-a"],
        )
    )
    workspace = SimpleNamespace(
        list_components_with_cases=_async_return(
            [
                {
                    "component_key": "ent-1",
                    "entity_ids": ["ent-1"],
                    "cases": ["case-a", "case-b"],
                }
            ]
        )
    )
    detector = _make_detector(env, workspace=workspace)
    summary = await detector.refresh_actor_recurrence()
    # actor_recurrence reconcile 不触碰 media_reuse signal
    assert summary["stale_deactivated"] == 0
    record = (await env.derived.list())[0]
    assert record.signal_type == "media_reuse"
    assert record.detector_active is True
    await env.db.dispose()


# ---------------------------------------------------------------------------
# S20-S22: SignalService 合流（§57/§57.1/§58）
# ---------------------------------------------------------------------------


class _FakeMonitorRepo:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    async def list_signal_rows(
        self, *, statuses=None, severity=None, case_id=None, rule_type=None, limit=None
    ) -> list[Any]:
        rows = self.rows
        if statuses:
            rows = [row for row in rows if row.alert.status in statuses]
        if severity:
            rows = [row for row in rows if row.severity == severity]
        if case_id:
            rows = [row for row in rows if row.case_id == case_id]
        if rule_type:
            rows = [row for row in rows if row.rule_type == rule_type]
        if limit:
            rows = rows[:limit]
        return rows

    async def set_alert_status(self, alert_id: str, status: str, *, by: str | None = None) -> Any:
        for row in self.rows:
            if row.alert.id == alert_id:
                row.alert.status = status
                return row.alert
        raise KeyError(alert_id)


def _monitor_row(signal_id: str, severity: str, status: str, case_id: str = "case-m") -> Any:
    now = datetime.now(UTC)
    return SimpleNamespace(
        alert=SimpleNamespace(
            id=signal_id,
            status=status,
            trigger_count=1,
            first_seen_at=now,
            last_seen_at=now,
            metric_snapshot={},
            explanation="",
            evidence_refs={},
        ),
        rule_type="absolute_volume",
        severity=severity,
        monitor_name="monitor-1",
        case_id=case_id,
        case_title="调查M",
    )


async def test_s20_merge_sorts_by_severity_then_detected_at() -> None:
    env = await _setup()
    monitors = _FakeMonitorRepo()
    monitors.rows = [_monitor_row("mon-1", "info", "open")]
    service = SignalService(env.db, monitors, derived_repository=env.derived)

    # 先建 info derived，再建 critical derived（detected_at 更新的在后）
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="d-1",
            fingerprint="fp-d1",
            signal_type="actor_recurrence",
            severity="info",
            case_id="case-a",
        )
    )
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="d-2",
            fingerprint="fp-d2",
            signal_type="media_reuse",
            severity="critical",
            case_id="case-b",
            related_case_ids=["case-b"],
        )
    )
    signals = await service.list_signals(limit=10)
    severities = [signal.severity for signal in signals]
    assert severities == ["critical", "info", "info"]  # critical derived 优先
    # source_label / detector fields
    derived = [signal for signal in signals if signal.source_type == "derived"]
    assert derived[0].source_label == "Media reuse"
    assert derived[0].detector_version == "advanced-signal-1.0.0"
    assert derived[0].detector_active is True
    assert derived[0].related_case_ids == ["case-b"]
    await env.db.dispose()


async def test_s21_case_filter_uses_case_links_join() -> None:
    env = await _setup()
    monitors = _FakeMonitorRepo()
    service = SignalService(env.db, monitors, derived_repository=env.derived)
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="d-1",
            fingerprint="fp-d1",
            related_case_ids=["case-a", "case-b"],
        )
    )
    by_a = await service.list_signals(case_id="case-a", limit=10)
    by_c = await service.list_signals(case_id="case-c", limit=10)
    assert len(by_a) == 1
    assert by_c == []
    await env.db.dispose()


async def test_s22_change_status_routes_to_derived_and_unknown_404() -> None:
    env = await _setup()
    monitors = _FakeMonitorRepo()
    service = SignalService(env.db, monitors, derived_repository=env.derived)
    record = await env.derived.upsert_observed_signal(
        **_payload(source_id="d-1", fingerprint="fp-d1")
    )
    acknowledged = await service.change_status(record.id, "acknowledge")
    assert acknowledged.status == "acknowledged"

    suppressed = await service.change_status(record.id, "suppress")
    assert suppressed.status == "suppressed"

    try:
        await service.get_signal("missing-id")
        raise AssertionError("expected signal_not_found")
    except ResourceNotFoundError as exc:
        assert exc.code == "signal_not_found"

    # monitor 路径不受影响
    monitors.rows = [_monitor_row("mon-1", "warning", "open")]
    resolved = await service.change_status("mon-1", "resolve")
    assert resolved.status == "resolved"
    assert resolved.source_type == "monitor_alert"
    await env.db.dispose()


# ---------------------------------------------------------------------------
# S23-S25: Derived Signal evidence 透传 + source filter 语义（Rework R6/R7）
# ---------------------------------------------------------------------------


async def test_s23_derived_signal_exposes_evidence_refs_items() -> None:
    """S23：GET 单条 derived signal 返回 evidence_refs.items（非空）。"""
    env = await _setup()
    service = SignalService(env.db, _FakeMonitorRepo(), derived_repository=env.derived)
    record = await env.derived.upsert_observed_signal(
        **_payload(
            source_id="d-ev",
            fingerprint="fp-dev",
            signal_type="media_reuse",
            evidence_refs=[{"sha256": "ab" * 32}, {"entity_id": "ent-1"}],
        )
    )
    signal = await service.get_signal(record.id)
    items = signal.evidence_refs.get("items")
    assert isinstance(items, list) and len(items) == 2
    assert {"sha256": "ab" * 32} in items
    assert {"entity_id": "ent-1"} in items
    await env.db.dispose()


async def test_s24_source_type_derived_plus_signal_type_media_reuse() -> None:
    """S24：source_type=derived + signal_type=media_reuse 过滤命中 media Signal。"""
    env = await _setup()
    monitors = _FakeMonitorRepo()
    monitors.rows = [_monitor_row("mon-1", "warning", "open")]
    service = SignalService(env.db, monitors, derived_repository=env.derived)
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="d-media",
            fingerprint="fp-dmedia",
            signal_type="media_reuse",
            title="同一媒体素材在多个调查中复用",
        )
    )
    await env.derived.upsert_observed_signal(
        **_payload(
            source_id="d-actor",
            fingerprint="fp-dactor",
            signal_type="actor_recurrence",
        )
    )
    media_signals = await service.list_signals(
        source_type="derived", signal_type="media_reuse", limit=10
    )
    assert len(media_signals) == 1
    assert media_signals[0].signal_type == "media_reuse"
    assert media_signals[0].source_type == "derived"
    await env.db.dispose()


async def test_s25_source_type_monitor_alert_excludes_derived() -> None:
    """S25：source_type=monitor_alert 不返回 derived 信号。"""
    env = await _setup()
    monitors = _FakeMonitorRepo()
    monitors.rows = [_monitor_row("mon-1", "warning", "open")]
    service = SignalService(env.db, monitors, derived_repository=env.derived)
    await env.derived.upsert_observed_signal(
        **_payload(source_id="d-media", fingerprint="fp-dmedia")
    )
    monitor_only = await service.list_signals(
        source_type="monitor_alert", limit=10
    )
    assert len(monitor_only) == 1
    assert all(s.source_type == "monitor_alert" for s in monitor_only)
    await env.db.dispose()
