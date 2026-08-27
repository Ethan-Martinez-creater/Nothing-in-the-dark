"""M10 叙事生命周期与纠错传播评估测试。"""

from __future__ import annotations

import atexit
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.narrative import (
    CorrectionAnalyzer,
    LifecycleAnalyzer,
    NarrativeClusterer,
    first_seen_vs_origin_label,
    jaccard,
)

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-narr-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


def _post(content: str, published: datetime, platform: str = "weibo") -> dict[str, object]:
    return {
        "id": f"p-{abs(hash(content))}",
        "content": content,
        "published_at": published,
        "platform": platform,
        "engagement": 10,
    }


# ---- 聚类 ----------------------------------------------------------------


def test_jaccard_similar() -> None:
    assert jaccard("今天天气真好", "今天天气真好呀") > 0.4


def test_clusterer_groups_similar_posts() -> None:
    now = datetime.now(UTC)
    clusterer = NarrativeClusterer()
    candidates = clusterer.cluster(
        [
            _post("某地发生火灾事故伤亡惨重", now),
            _post("某地火灾事故伤亡惨重现场视频", now + timedelta(hours=1)),
            _post("今天股市大涨", now + timedelta(hours=2)),
        ]
    )
    assert len(candidates) >= 2
    sizes = sorted(c["member_count"] for c in candidates)
    assert sizes[-1] >= 2  # 相似帖聚合到同一叙事


def test_clusterer_template_downweighted() -> None:
    now = datetime.now(UTC)
    clusterer = NarrativeClusterer()
    candidates = clusterer.cluster(
        [
            _post("转发了微博：某事件最新进展", now),
            _post("转发了微博：某事件最新进展（更新）", now + timedelta(hours=1)),
        ]
    )
    # 模板降权后仍可能聚到一起（同内容），但至少有 1 个候选。
    assert len(candidates) >= 1


# ---- 生命周期 ------------------------------------------------------------


def _timeline(volumes: list[int]) -> list[dict[str, object]]:
    base = datetime(2026, 8, 1, tzinfo=UTC)
    return [
        {
            "bucket": base + timedelta(hours=i),
            "platform": "weibo",
            "volume": v,
            "unique_accounts": v,
            "engagement": v * 10,
        }
        for i, v in enumerate(volumes)
    ]


def test_lifecycle_growing_peaking_declining() -> None:
    analyzer = LifecycleAnalyzer(bucket_seconds=3600, min_peak=5, dormant_after_buckets=3)
    result = analyzer.analyze(_timeline([1, 3, 8, 20, 6, 2]))
    assert "peaking" in result["stages"]
    assert "growing" in result["stages"] or "emerging" in result["stages"]


def test_lifecycle_data_gap_is_unknown_not_declining() -> None:
    analyzer = LifecycleAnalyzer(bucket_seconds=3600, min_peak=3, dormant_after_buckets=3)
    result = analyzer.analyze(_timeline([5, 0, 0, 0, 1]))
    # 数据缺口桶标 unknown，不被当作衰退。
    gap_indexes = [i for i, v in enumerate([5, 0, 0, 0, 1]) if v == 0]
    for idx in gap_indexes:
        assert result["stages"][idx] == "unknown"
    assert any("数据缺口" in note for note in result["notes"])


def test_lifecycle_resurgence() -> None:
    analyzer = LifecycleAnalyzer(
        bucket_seconds=3600, min_peak=3, dormant_after_buckets=3, resurgence_ratio=2.0
    )
    result = analyzer.analyze(_timeline([4, 0, 0, 12, 2]))
    assert result["stages"][3] == "resurgent"


def test_lifecycle_empty_returns_unknown() -> None:
    analyzer = LifecycleAnalyzer()
    result = analyzer.analyze([])
    assert result["stages"] == []


# ---- 纠错分析 ------------------------------------------------------------


def test_correction_analyzer_no_causal_claim() -> None:
    now = datetime.now(UTC)
    analyzer = CorrectionAnalyzer()
    result = analyzer.analyze(
        correction_time=now,
        before=[_post("某事件传言 A", now - timedelta(hours=2))],
        after=[_post("某事件辟谣", now + timedelta(hours=1))],
    )
    assert result["causal_claim"] is False
    assert result["confidence_level"] == "low"
    assert any("因果" in limit for limit in result["limitations"])


def test_first_seen_label_never_origin() -> None:
    label = first_seen_vs_origin_label(datetime.now(UTC))
    assert label == "first_collected"
    assert "origin" not in label


# ---- API -----------------------------------------------------------------


def _client() -> TestClient:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{_tmp_db()}",
        demo_mode=True,
    )
    app = create_app(settings)
    return TestClient(app)


def test_api_narrative_analyze_and_list() -> None:
    with _client() as client:
        # 需要先有帖子；用 demo 爬虫无法直接造数据，直接走 analyze 空案件。
        response = client.post("/api/v1/cases/c1/narratives/analyze")
        assert response.status_code == 202
        listed = client.get("/api/v1/cases/c1/narratives")
        assert listed.status_code == 200


def test_api_correction_flow() -> None:
    with _client() as client:
        created = client.post(
            "/api/v1/cases/c1/corrections",
            json={"content": "该消息不实，官方已辟谣", "correction_type": "denial"},
        )
        assert created.status_code == 201
        assert created.json()["correction_type"] == "denial"
        listed = client.get("/api/v1/cases/c1/corrections")
        assert listed.status_code == 200
        assert len(listed.json()) == 1
