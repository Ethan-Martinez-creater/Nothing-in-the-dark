"""M10: domain evaluation framework over synthetic labelled datasets."""

from __future__ import annotations

from app.services.analysis import verify_claims
from app.services.evaluation import (
    PRF,
    classification_report,
    evaluate_claim_cards,
    evaluate_no_evidence_refusal,
    evaluate_origin_candidates,
    evaluate_propagation_edges,
    evaluate_report_citations,
    evaluate_sentiment,
    evaluate_stance,
    precision_recall_f1,
    ranking_at_k,
)
from app.services.propagation_algorithm import compute_inferred_edges
from tests.synthetic_eval_dataset import (
    CLAIM_CARDS,
    KNOWN_EVIDENCE_IDS,
    ORIGIN_EDGES,
    ORIGIN_POSTS,
    ORIGIN_RELEVANT,
    PROPAGATION_EXPECTED,
    PROPAGATION_POSTS,
    REFUSAL_CARDS,
    REPORTS,
    SENTIMENT_CORPUS,
    STANCE_CORPUS,
)

# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------


def test_precision_recall_f1_basic() -> None:
    assert precision_recall_f1(tp=4, fp=1, fn=2) == PRF(
        precision=0.8, recall=0.6667, f1=0.7273, support=6
    )


def test_precision_recall_f1_zero_denominators() -> None:
    assert precision_recall_f1(tp=0, fp=0, fn=0) == PRF(0.0, 0.0, 0.0, 0)
    assert precision_recall_f1(tp=3, fp=0, fn=0) == PRF(1.0, 1.0, 1.0, 3)
    assert precision_recall_f1(tp=0, fp=2, fn=3) == PRF(0.0, 0.0, 0.0, 3)


def test_classification_report_small_sample() -> None:
    report = classification_report(
        labels_true=["a", "a", "b", "b", "b"],
        labels_pred=["a", "b", "b", "b", "b"],
        classes=["a", "b"],
    )
    assert report["accuracy"] == 0.8
    assert report["a"] == {"precision": 1.0, "recall": 0.5, "f1": 0.6667, "support": 2}
    assert report["b"] == {"precision": 0.75, "recall": 1.0, "f1": 0.8571, "support": 3}
    assert report["macro_f1"] == round((0.6667 + 0.8571) / 2, 4)


# ---------------------------------------------------------------------------
# Sentiment / stance classification
# ---------------------------------------------------------------------------


def test_sentiment_evaluation_reaches_high_scores_on_synthetic_corpus() -> None:
    report = evaluate_sentiment(SENTIMENT_CORPUS)
    # The corpus is lexicon-derived, so a correct implementation scores at
    # or very near 1.0; the threshold leaves room for boundary drift.
    assert report["accuracy"] >= 0.9
    assert report["macro_f1"] >= 0.9
    for label in ("positive", "neutral", "negative"):
        assert report[label]["support"] > 0


def test_sentiment_handles_negation_and_intensifier_flips() -> None:
    # Explicit probes for the trickiest lexicon behaviours.
    assert evaluate_sentiment([("不是好评，而是差评", "negative")])["accuracy"] == 1.0
    assert evaluate_sentiment([("并非差评，整体还行", "positive")])["accuracy"] == 1.0
    assert evaluate_sentiment([("非常满意这次服务", "positive")])["accuracy"] == 1.0


def test_stance_evaluation_reaches_high_scores_on_synthetic_corpus() -> None:
    report = evaluate_stance(STANCE_CORPUS)
    assert report["accuracy"] >= 0.9
    assert report["macro_f1"] >= 0.9
    for label in ("supportive", "opposing", "questioning", "neutral"):
        assert report[label]["support"] > 0


# ---------------------------------------------------------------------------
# Propagation edges
# ---------------------------------------------------------------------------


def test_propagation_edge_precision_recall_on_synthetic_chain() -> None:
    report = evaluate_propagation_edges(PROPAGATION_POSTS, PROPAGATION_EXPECTED)
    assert report["expected"] == 3
    assert report["predicted"] == 3
    # The chain p1->p2 (inferred), p1->p3 (observed retweet), p2->p3
    # (inferred) is fully recovered; no edges leak to p4 (no entities) or
    # p5 (outside the 168h time window).
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0


def test_propagation_refuses_out_of_window_and_entity_less_posts() -> None:
    pairs = {
        frozenset({edge.source_post_id, edge.target_post_id})
        for edge in compute_inferred_edges(PROPAGATION_POSTS)
    }
    assert all("p4" not in pair and "p5" not in pair for pair in pairs)
    assert frozenset({"p5", "p1"}) not in pairs


# ---------------------------------------------------------------------------
# Claim cards: citation correctness & no-evidence refusal
# ---------------------------------------------------------------------------


def test_claim_citation_correctness_counts_only_real_references() -> None:
    report = evaluate_claim_cards(CLAIM_CARDS, KNOWN_EVIDENCE_IDS)
    # claim-1 cites ev-1 + post-1 (valid); claim-2 cites ev-2 (valid) +
    # post-9 (dangling); claim-3 cites nothing.
    assert report["citation_correctness"] == 0.75
    assert report["valid_references"] == 3
    assert report["total_references"] == 4
    assert report["fully_valid_cards"] == 1
    assert report["cards_with_references"] == 2


def test_no_evidence_refusal_rate_penalises_over_claiming() -> None:
    report = evaluate_no_evidence_refusal(REFUSAL_CARDS)
    # Three evidence-less cards; two refuse (insufficient / old_news) and
    # one over-claims (credible without evidence).
    assert report["evidence_less_cards"] == 3
    assert report["refused_cards"] == 2
    assert report["refusal_rate"] == round(2 / 3, 4)


async def test_verify_claims_integration_refuses_without_repository() -> None:
    """Real pipeline: no repository -> every card verdicts insufficient."""
    result = await verify_claims(PROPAGATION_POSTS, "财报")
    assert result["cards"]  # claims were extracted from the synthetic posts
    assert all(card["verdict"] == "insufficient" for card in result["cards"])
    report = evaluate_no_evidence_refusal(result["cards"])
    # Every card cites its source post, so none is evidence-less; the
    # refusal guarantee is enforced at verdict level instead.
    assert report["evidence_less_cards"] == 0


# ---------------------------------------------------------------------------
# Report citation coverage
# ---------------------------------------------------------------------------


def test_report_citation_coverage_across_fixtures() -> None:
    report_a = evaluate_report_citations(REPORTS[0][0], REPORTS[0][1])
    assert report_a["citation_coverage"] == round(2 / 3, 4)
    assert report_a["unresolved"] == ["ghost-3"]

    report_b = evaluate_report_citations(REPORTS[1][0], REPORTS[1][1])
    assert report_b["citation_coverage"] == 0.0
    assert report_b["cited"] == 0

    report_c = evaluate_report_citations(REPORTS[2][0], REPORTS[2][1])
    assert report_c["citation_coverage"] == 1.0
    assert report_c["unresolved"] == []


# ---------------------------------------------------------------------------
# Origin-candidate Top-K ranking
# ---------------------------------------------------------------------------


def test_ranking_at_k_uses_k_as_precision_denominator() -> None:
    score = ranking_at_k(["s1", "noise"], {"s1", "h1"}, k=3)
    assert score["hits"] == 1
    assert score["precision"] == round(1 / 3, 4)
    assert score["recall"] == 0.5


def test_origin_candidates_topk_puts_earliest_first_and_excludes_noise() -> None:
    report = evaluate_origin_candidates(
        ORIGIN_POSTS, ORIGIN_EDGES, ORIGIN_RELEVANT, ks=(1, 3, 5)
    )
    assert report["ranked"][0] == "s1"
    assert set(report["ranked"]) == ORIGIN_RELEVANT
    assert "noise1" not in report["ranked"]
    assert report["at_k"]["1"]["precision"] == 1.0
    assert report["at_k"]["1"]["recall"] == round(1 / 3, 4)
    assert report["at_k"]["3"]["precision"] == 1.0
    assert report["at_k"]["3"]["recall"] == 1.0
    # Only 3 candidates exist; P@5 still divides by 5.
    assert report["at_k"]["5"]["precision"] == round(3 / 5, 4)
    assert report["at_k"]["5"]["recall"] == 1.0
