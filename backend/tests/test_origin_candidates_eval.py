"""M10b 遗留补丁：源头候选 Top-K 评测。

``compute_origin_candidates``（M7 已实现）返回「发布时间最早的帖」+「高出度
hub」作为源头候选。本文件用带 ground truth 的合成传播图评测其精确率/召回率：
预期集合 = {最早帖} ∪ {out_degree ≥ 2 的 hub}，其余节点（迟到、低出度）不得
混入候选。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.evaluation import ranking_at_k
from app.services.propagation_algorithm import (
    EdgeCandidate,
    compute_origin_candidates,
)


def _post(post_id: str, offset_hours: int) -> dict:
    base = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    return {
        "id": post_id,
        "author": "作者",
        "platform": "weibo",
        "content": f"内容 {post_id}",
        "published_at": (base + timedelta(hours=offset_hours)).isoformat(),
    }


def _edge(source: str, target: str) -> EdgeCandidate:
    return EdgeCandidate(
        source_post_id=source,
        target_post_id=target,
        relation="inferred",
        confidence=0.5,
        feature_scores={},
        reasons=["测试"],
        evidence_ids=[source, target],
    )


def _precision_recall(
    predicted: list[str],
    ground_truth: set[str],
) -> tuple[float, float]:
    # 集合级 P/R：K = 预测长度，与 ranking_at_k 在该 K 上一致。
    if not predicted:
        return 0.0, 0.0
    score = ranking_at_k(predicted, ground_truth, k=len(predicted))
    return score["precision"], score["recall"]


def _build_scenario(
    earliest: str,
    hubs: list[str],
    noise: list[str],
) -> tuple[list[dict], list[EdgeCandidate], set[str]]:
    nodes: list[dict] = []
    edges: list[EdgeCandidate] = []
    ground_truth = {earliest, *hubs}
    nodes.append(_post(earliest, offset_hours=0))
    for hub in hubs:
        # 每个 hub 有 3 条出边，指向独立的下游节点。
        nodes.append(_post(hub, offset_hours=1))
        for downstream in range(3):
            child = f"{hub}-child-{downstream}"
            nodes.append(_post(child, offset_hours=2))
            edges.append(_edge(hub, child))
    for i, noise_id in enumerate(noise):
        nodes.append(_post(noise_id, offset_hours=48 + i))
    return nodes, edges, ground_truth


def test_origin_candidates_recall_and_precision_on_labeled_graph() -> None:
    """评测主场景：最早帖 + hub 全命中，噪声不混入（P=R=1.0）。"""
    nodes, edges, ground_truth = _build_scenario(
        earliest="s1",
        hubs=["h1", "h2"],
        noise=["noise1", "noise2"],
    )
    candidates = compute_origin_candidates(nodes, edges)
    predicted = [item["node_id"] for item in candidates]

    assert predicted[0] == "s1"
    precision, recall = _precision_recall(predicted, ground_truth)
    assert precision == 1.0
    assert recall == 1.0


def test_origin_candidates_keeps_earliest_post_even_without_out_degree() -> None:
    """最早帖即使无出度也必须进入候选（发布时间是源头首要信号）。"""
    nodes, edges, _ = _build_scenario(
        earliest="s1",
        hubs=[],
        noise=["late1"],
    )
    candidates = compute_origin_candidates(nodes, edges)
    assert candidates[0]["node_id"] == "s1"
    assert len(candidates) == 1
    assert "最早" in candidates[0]["reason"]


def test_origin_candidates_excludes_late_low_degree_posts() -> None:
    """迟到且低出度的帖不进入候选；只保留最早帖。"""
    nodes, edges, ground_truth = _build_scenario(
        earliest="s1",
        hubs=[],
        noise=["noise1", "noise2", "noise3"],
    )
    predicted = [item["node_id"] for item in compute_origin_candidates(nodes, edges)]
    precision, recall = _precision_recall(predicted, ground_truth)
    assert precision == 1.0
    assert recall == 1.0
    assert predicted == ["s1"]


def test_origin_candidates_caps_hubs_at_three_earliest() -> None:
    """hub 超过 3 个时按时间序只取前 3。"""
    hubs = [f"h{i}" for i in range(5)]
    nodes: list[dict] = []
    edges: list[EdgeCandidate] = []
    for hub in hubs:
        nodes.append(_post(hub, offset_hours=1))
        nodes.append(_post(f"{hub}-child", offset_hours=2))
        edges.append(_edge(hub, f"{hub}-child"))
        edges.append(_edge(hub, f"{hub}-child2",))
        edges.append(_edge(hub, f"{hub}-child3",))
    # 每个 hub 3 条出边；最早帖由 s1 担任。
    nodes.append(_post("s1", offset_hours=0))
    candidates = compute_origin_candidates(nodes, edges)
    predicted = [item["node_id"] for item in candidates]
    assert predicted[0] == "s1"
    assert predicted[1:] == hubs[:3]
    assert len(predicted) == 4


def test_origin_candidates_confidence_bounds() -> None:
    """置信度边界：最早帖 0.5+0.05*out_degree（上限 0.7），hub 0.4+0.1*
    out_degree（上限 0.8）。"""
    nodes = [
        _post("s1", offset_hours=0),
        _post("h1", offset_hours=1),
        _post("c1", offset_hours=2),
        _post("c2", offset_hours=2),
        _post("c3", offset_hours=2),
        _post("c4", offset_hours=2),
        _post("c5", offset_hours=2),
        _post("c6", offset_hours=2),
        _post("c7", offset_hours=2),
        _post("c8", offset_hours=2),
        _post("c9", offset_hours=2),
        _post("c10", offset_hours=2),
    ]
    edges = [_edge("h1", f"c{i}") for i in range(1, 11)] + [
        _edge("s1", f"c{i}") for i in range(1, 9)
    ]
    candidates = {
        item["node_id"]: item["confidence"] for item in compute_origin_candidates(
            nodes, edges
        )
    }
    # s1 出度 8 → 0.5 + 0.4 = 0.9，cap 0.7
    assert candidates["s1"] == 0.7
    # h1 出度 10 → 0.4 + 1.0 = 1.4，cap 0.8
    assert candidates["h1"] == 0.8


def test_origin_candidates_empty_and_single_node() -> None:
    assert compute_origin_candidates([], []) == []
    single = [_post("only", offset_hours=0)]
    candidates = compute_origin_candidates(single, [])
    assert [item["node_id"] for item in candidates] == ["only"]
