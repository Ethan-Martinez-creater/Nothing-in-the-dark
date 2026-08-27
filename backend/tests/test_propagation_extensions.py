"""M7c propagation extensions: media fingerprints, cross-platform account
mapping, rule-based edge critic and node role labelling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.propagation_algorithm import (
    ALGORITHM_VERSION,
    EdgeCandidate,
    build_propagation_graph,
    compute_node_roles,
    criticize_edges,
    map_cross_platform_accounts,
    media_fingerprints,
    normalize_account_name,
    normalize_media_url,
    url_fingerprint,
)


def _post(
    post_id: str,
    *,
    author: str = "作者",
    platform: str = "weibo",
    content: str = "内容",
    published_at: str | None = None,
    image_url: str | None = None,
) -> dict:
    return {
        "id": post_id,
        "author": author,
        "platform": platform,
        "content": content,
        "published_at": published_at or "2026-08-01T00:00:00+00:00",
        "image_url": image_url,
    }


# ---------- media URL normalization and fingerprints ----------


def test_normalize_media_url_strips_tracking_noise() -> None:
    raw = "HTTPS://CDN.EXAMPLE.COM/IMG/A.PNG?token=abc123&spm=1234&width=100#frag"
    assert normalize_media_url(raw) == "https://cdn.example.com/img/a.png?width=100"
    assert normalize_media_url("https://cdn.example.com/img/a.png") == (
        "https://cdn.example.com/img/a.png"
    )
    assert normalize_media_url("") == ""


def test_url_fingerprint_is_deterministic_and_token_insensitive() -> None:
    assert url_fingerprint("https://cdn.example.com/a.png?token=1") == (
        url_fingerprint("https://cdn.example.com/a.png?token=2")
    )
    assert url_fingerprint("https://cdn.example.com/a.png") == (
        url_fingerprint("https://cdn.example.com/a.png")
    )


def test_media_fingerprints_aggregate_cross_post_reuse() -> None:
    posts = [
        _post("p1", platform="weibo", image_url="https://cdn.x.com/m.png?token=1"),
        _post("p2", platform="bilibili", image_url="https://cdn.x.com/m.png?token=2"),
        _post("p3", image_url="https://cdn.x.com/other.png"),
    ]
    fingerprints = media_fingerprints(posts)
    assert len(fingerprints) == 2
    reused = next(item for item in fingerprints if "m.png" in item["url"])
    assert reused["fingerprint"] == url_fingerprint("https://cdn.x.com/m.png")
    assert set(reused["platforms"]) == {"weibo", "bilibili"}
    assert reused["post_ids"] == ["p1", "p2"]


# ---------- account mapping ----------


def test_normalize_account_name_folds_case_and_noise() -> None:
    assert normalize_account_name("人民日报_官方") == "人民日报官方"
    assert normalize_account_name("  Weibo:TechNews  ") == "technews"
    assert normalize_account_name("TechNews的微博") == "technews"


def test_map_cross_platform_accounts_merges_similar_names() -> None:
    posts = [
        _post("p1", author="TechNews", platform="weibo"),
        _post("p2", author="Tech_News", platform="bilibili"),
        _post("p3", author="Tech News", platform="weibo"),
        _post("p4", author="完全无关账号", platform="weibo"),
    ]
    groups = map_cross_platform_accounts(posts)
    top = groups[0]
    assert top["cross_platform"] is True
    assert top["platforms"] == ["bilibili", "weibo"]
    assert top["post_count"] == 3
    assert len(groups) == 2


# ---------- edge critic ----------


def _edge(
    source: str,
    target: str,
    relation: str = "observed",
    similarity: float = 0.0,
    overlap: float = 0.0,
) -> EdgeCandidate:
    return EdgeCandidate(
        source_post_id=source,
        target_post_id=target,
        relation=relation,
        confidence=0.8,
        feature_scores={
            "text_similarity": similarity,
            "entity_overlap": overlap,
        },
        reasons=["测试"],
        evidence_ids=[source, target],
    )


def test_critic_rejects_observed_edge_with_inverted_time() -> None:
    posts = [
        _post("p1", published_at="2026-08-02T00:00:00+00:00"),
        _post("p2", published_at="2026-08-01T00:00:00+00:00"),
    ]
    edge = _edge("p1", "p2")  # p2 (target) predates p1 (source)
    result = criticize_edges(posts, [edge])
    assert len(result["rejected"]) == 1
    assert result["kept"] == []
    assert result["rejected"][0]["reason"].startswith("observed")


def test_critic_rejects_low_evidence_inferred_edge() -> None:
    posts = [_post("p1"), _post("p2")]
    edge = _edge("p1", "p2", relation="inferred")
    result = criticize_edges(posts, [edge])
    assert len(result["rejected"]) == 1


def test_critic_keeps_media_reuse_edge_and_boosts_confidence() -> None:
    posts = [
        _post("p1", image_url="https://cdn.x.com/m.png"),
        _post("p2", image_url="https://cdn.x.com/m.png"),
    ]
    edge = _edge("p1", "p2", relation="inferred")
    result = criticize_edges(posts, [edge])
    assert len(result["rejected"]) == 0
    assert result["kept"][0]["confidence"] > 0.8
    assert "媒体指纹" in result["kept"][0]["reasons"][-1]


# ---------- node roles ----------


def test_node_roles_label_source_bridge_and_burst() -> None:
    base = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    posts = [
        _post("s1", published_at=(base).isoformat()),
        _post("b1", published_at=(base + timedelta(hours=1)).isoformat()),
        _post("b2", published_at=(base + timedelta(hours=2)).isoformat()),
        _post("t1", published_at=(base + timedelta(hours=3)).isoformat()),
        _post("t2", published_at=(base + timedelta(hours=4)).isoformat()),
    ]
    edges = [
        _edge("s1", "b1", relation="observed"),
        _edge("b1", "b2", relation="observed"),
        _edge("s1", "t1", relation="inferred"),
        _edge("s1", "t2", relation="inferred"),
    ]
    roles = {
        item["post_id"]: item["role"] for item in compute_node_roles(posts, edges)
    }
    assert roles["s1"] == "source"
    assert roles["b1"] == "bridge"  # in-degree 1 and out-degree 1
    assert roles["s1"] != "bridge"


# ---------- graph integration ----------


def test_build_propagation_graph_appends_m7c_keys() -> None:
    posts = [
        _post("p1", author="TechNews", image_url="https://cdn.x.com/m.png"),
        _post("p2", author="TechNews", platform="bilibili"),
    ]
    graph = build_propagation_graph(posts)
    assert "media_fingerprints" in graph
    assert "account_groups" in graph
    assert "critique" in graph
    assert "node_roles" in graph
    assert graph["account_groups"][0]["cross_platform"] is True
    assert graph["algorithm_version"] == ALGORITHM_VERSION
