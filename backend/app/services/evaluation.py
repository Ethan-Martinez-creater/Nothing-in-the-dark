"""M10: evaluation toolkit — P/R/F1 scoring for domain algorithms.

Pure, dependency-free metrics so the deterministic domain algorithms can
be measured against labelled synthetic datasets:

* sentiment / stance classification -> per-class P/R/F1, macro and accuracy;
* propagation edges -> edge-level precision / recall / F1;
* claim cards -> citation correctness and no-evidence refusal rate;
* reports -> citation coverage against real Evidence / Claim ids;
* origin candidates -> ranking P@K / R@K against labelled sources.

Nothing here talks to a database or an LLM; the framework only scores
the output of the pure algorithms in ``app/services``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.services.classifiers import SentimentClassifier, StanceClassifier
from app.services.propagation_algorithm import (
    EdgeCandidate,
    compute_inferred_edges,
    compute_origin_candidates,
    extract_observed_edges,
)


@dataclass(frozen=True, slots=True)
class PRF:
    """One class's precision / recall / F1 with support."""

    precision: float
    recall: float
    f1: float
    support: int


def precision_recall_f1(tp: int, fp: int, fn: int) -> PRF:
    """F1 with harmonic-mean edge cases handled (zero denominator -> 0)."""
    support = tp + fn
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return PRF(round(precision, 4), round(recall, 4), round(f1, 4), support)


def classification_report(
    labels_true: list[str],
    labels_pred: list[str],
    classes: list[str],
) -> dict[str, Any]:
    """Per-class P/R/F1 plus macro average and overall accuracy."""
    assert len(labels_true) == len(labels_pred)
    report: dict[str, Any] = {}
    correct = sum(
        1 for expected, actual in zip(labels_true, labels_pred, strict=True)
        if expected == actual
    )
    report["accuracy"] = round(correct / len(labels_true), 4) if labels_true else 0.0
    macro: list[PRF] = []
    for label in classes:
        tp = sum(
            1
            for expected, actual in zip(labels_true, labels_pred, strict=True)
            if actual == label and expected == label
        )
        fp = sum(
            1
            for expected, actual in zip(labels_true, labels_pred, strict=True)
            if actual == label and expected != label
        )
        fn = sum(
            1
            for expected, actual in zip(labels_true, labels_pred, strict=True)
            if actual != label and expected == label
        )
        score = precision_recall_f1(tp, fp, fn)
        report[label] = {
            "precision": score.precision,
            "recall": score.recall,
            "f1": score.f1,
            "support": score.support,
        }
        macro.append(score)
    if macro:
        report["macro_f1"] = round(
            sum(score.f1 for score in macro) / len(macro), 4
        )
    else:
        report["macro_f1"] = 0.0
    return report


def evaluate_sentiment(
    corpus: list[tuple[str, str]],
) -> dict[str, Any]:
    """Score :class:`SentimentClassifier` against a labelled corpus.

    ``corpus`` is ``[(text, expected_label)]`` with labels
    ``positive | neutral | negative``.
    """
    labels_true = [label for _, label in corpus]
    labels_pred = [SentimentClassifier.classify(text)[0] for text, _ in corpus]
    return classification_report(
        labels_true, labels_pred, classes=["positive", "neutral", "negative"]
    )


def evaluate_stance(
    corpus: list[tuple[str, str]],
) -> dict[str, Any]:
    """Score :class:`StanceClassifier` against a labelled corpus.

    Labels: ``supportive | opposing | questioning | neutral``.
    """
    labels_true = [label for _, label in corpus]
    labels_pred = [StanceClassifier.classify(text) for text, _ in corpus]
    return classification_report(
        labels_true,
        labels_pred,
        classes=["supportive", "opposing", "questioning", "neutral"],
    )


def _undirected_pair(edge: EdgeCandidate) -> frozenset[str]:
    return frozenset({edge.source_post_id, edge.target_post_id})


def evaluate_propagation_edges(
    posts: list[dict[str, Any]],
    expected_pairs: set[frozenset[str]],
) -> dict[str, Any]:
    """Edge-level precision / recall of observed + inferred edges.

    ``expected_pairs`` holds undirected ``frozenset({source, target})``
    pairs that genuinely propagated. The score is computed on undirected
    pairs because edge direction is secondary to whether two posts are
    connected at all.
    """
    candidates = [
        *extract_observed_edges(posts),
        *compute_inferred_edges(posts),
    ]
    predicted = {_undirected_pair(edge) for edge in candidates}
    tp = len(predicted & expected_pairs)
    fp = len(predicted - expected_pairs)
    fn = len(expected_pairs - predicted)
    score = precision_recall_f1(tp, fp, fn)
    return {
        "precision": score.precision,
        "recall": score.recall,
        "f1": score.f1,
        "predicted": len(predicted),
        "expected": len(expected_pairs),
    }


def evaluate_claim_cards(
    cards: list[dict[str, Any]],
    known_evidence_ids: set[str],
) -> dict[str, Any]:
    """Citation correctness: do the cards only cite evidence that exists?

    A card cites only real ids when every supporting and contradicting
    evidence reference is present in ``known_evidence_ids``.
    """
    total_references = 0
    valid_references = 0
    fully_valid_cards = 0
    for card in cards:
        references = [
            *(card.get("supporting_evidence") or []),
            *(card.get("contradicting_evidence") or []),
        ]
        if not references:
            continue
        total_references += len(references)
        valid = all(ref in known_evidence_ids for ref in references)
        valid_references += sum(1 for ref in references if ref in known_evidence_ids)
        if valid:
            fully_valid_cards += 1
    return {
        "citation_correctness": round(
            valid_references / total_references, 4
        ) if total_references else 1.0,
        "valid_references": valid_references,
        "total_references": total_references,
        "fully_valid_cards": fully_valid_cards,
        "cards_with_references": sum(
            1
            for card in cards
            if (card.get("supporting_evidence") or card.get("contradicting_evidence"))
        ),
    }


def evaluate_no_evidence_refusal(
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    """Refusal rate: claims without evidence must not be confirmed.

    A card is "without evidence" when it carries no supporting evidence;
    refusing means its verdict is not a confident confirmation. The rule
    verdicts come from :func:`verify_claims`, where only ``credible``
    (authoritative source) is a confirmation — everything else
    (``insufficient``, ``old_news``, None) is a refusal to over-claim.
    """
    evidence_less = [
        card
        for card in cards
        if not card.get("supporting_evidence")
        and not card.get("contradicting_evidence")
    ]
    refused = [
        card for card in evidence_less if card.get("verdict") != "credible"
    ]
    return {
        "refusal_rate": round(len(refused) / len(evidence_less), 4)
        if evidence_less
        else 1.0,
        "evidence_less_cards": len(evidence_less),
        "refused_cards": len(refused),
    }


def evaluate_report_citations(
    report: dict[str, Any],
    known_ids: set[str],
) -> dict[str, Any]:
    """Report citation coverage: how many cited links resolve to real ids.

    ``report["citation_links"]`` is a list of evidence/claim ids the report
    binds its conclusions to; coverage is the fraction that actually exist.
    """
    raw_links = report.get("citation_links") or []
    links: list[str] = []
    for link in raw_links:
        if isinstance(link, dict):
            links.extend(str(item) for item in (link.get("evidence_ids") or []) if item)
        elif link:
            links.append(str(link))
    if not links:
        return {
            "citation_coverage": 0.0,
            "cited": 0,
            "resolved": 0,
            "unresolved": [],
        }
    unresolved = [link for link in links if link not in known_ids]
    return {
        "citation_coverage": round((len(links) - len(unresolved)) / len(links), 4),
        "cited": len(links),
        "resolved": len(links) - len(unresolved),
        "unresolved": unresolved,
    }


def ranking_at_k(
    ranked_ids: list[str],
    relevant: set[str],
    k: int,
) -> dict[str, Any]:
    """Standard IR P@K / R@K on an ordered candidate list.

    Precision@K uses ``k`` as the denominator even when fewer than ``k``
    items were returned, so a short ranking cannot inflate the score.
    """
    if k <= 0:
        raise ValueError("k must be a positive integer")
    window = list(ranked_ids[:k])
    hits = sum(1 for item in window if item in relevant)
    return {
        "k": k,
        "hits": hits,
        "precision": round(hits / k, 4),
        "recall": round(hits / len(relevant), 4) if relevant else 0.0,
        "ranked": window,
    }


def evaluate_origin_candidates(
    posts: list[dict[str, Any]],
    edges: list[EdgeCandidate],
    relevant: set[str],
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict[str, Any]:
    """Rank ``compute_origin_candidates`` and score P@K / R@K.

    The algorithm's returned order is the ranking: earliest post first,
    then high out-degree hubs in time order.
    """
    ranked = [
        str(item["node_id"])
        for item in compute_origin_candidates(posts, edges)
    ]
    return {
        "ranked": ranked,
        "candidate_count": len(ranked),
        "relevant_count": len(relevant),
        "at_k": {str(k): ranking_at_k(ranked, relevant, k) for k in ks},
    }


# ---------------------------------------------------------------------------
# M20: Evaluator registry —— 保持旧 API 兼容的增量扩展。
# ---------------------------------------------------------------------------

EvaluatorFn = Callable[[list[dict[str, Any]], dict[str, Any]], dict[str, Any]]


class EvaluatorDefinition:
    """一个评测器：名称/版本/指标/确定性/阈值/依赖。"""

    def __init__(
        self,
        name: str,
        metric: str,
        fn: EvaluatorFn,
        *,
        version: str = "1.0",
        deterministic: bool = True,
        thresholds: dict[str, float] | None = None,
        dependencies: list[str] | None = None,
    ) -> None:
        self.name = name
        self.metric = metric
        self.fn = fn
        self.version = version
        self.deterministic = deterministic
        self.thresholds = dict(thresholds or {})
        self.dependencies = list(dependencies or [])

    def evaluate(
        self, examples: list[dict[str, Any]], config: dict[str, Any]
    ) -> dict[str, Any]:
        return self.fn(examples, config)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "metric": self.metric,
            "deterministic": self.deterministic,
            "thresholds": self.thresholds,
            "dependencies": self.dependencies,
        }


class EvaluatorRegistry:
    """注册与运行评测器；崩溃只使对应任务失败，不伪造整体通过。"""

    def __init__(self) -> None:
        self._evaluators: dict[str, EvaluatorDefinition] = {}

    def register(self, evaluator: EvaluatorDefinition) -> None:
        if evaluator.name in self._evaluators:
            raise ValueError("Evaluator already registered: " + evaluator.name)
        self._evaluators[evaluator.name] = evaluator

    def names(self) -> list[str]:
        return sorted(self._evaluators)

    def get(self, name: str) -> EvaluatorDefinition:
        try:
            return self._evaluators[name]
        except KeyError as exc:
            raise ValueError("Unknown evaluator: " + name) from exc

    def run_suite(
        self,
        evaluator_names: list[str],
        examples: list[dict[str, Any]],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """逐评测器运行；单个失败只标记该任务，不伪造整体通过。"""
        results: dict[str, Any] = {}
        failed: list[dict[str, object]] = []
        for name in evaluator_names:
            evaluator = self.get(name)
            try:
                results[name] = evaluator.evaluate(examples, config or {})
            except Exception as exc:  # noqa: BLE001 - evaluator crash isolation
                failed.append(
                    {"evaluator": name, "error": type(exc).__name__ + ": " + str(exc)[:200]}
                )
        return {
            "results": results,
            "failed": failed,
            "all_passed": not failed,
        }


def build_default_registry() -> EvaluatorRegistry:
    """注册现有确定性评测器（评估分类/传播/引用/不足回答）。"""
    registry = EvaluatorRegistry()

    def _sentiment(examples: list[dict[str, Any]], _config: dict[str, Any]) -> dict[str, Any]:
        corpus = [
            (str(example.get("input") or ""), str(example.get("gold") or ""))
            for example in examples
            if example.get("input") and example.get("gold")
        ]
        return evaluate_sentiment(corpus)

    registry.register(
        EvaluatorDefinition(
            "sentiment",
            "macro_f1",
            _sentiment,
            thresholds={"macro_f1": 0.6},
        )
    )

    def _stance(examples: list[dict[str, Any]], _config: dict[str, Any]) -> dict[str, Any]:
        corpus = [
            (str(example.get("input") or ""), str(example.get("gold") or ""))
            for example in examples
            if example.get("input") and example.get("gold")
        ]
        return evaluate_stance(corpus)

    registry.register(
        EvaluatorDefinition(
            "stance",
            "macro_f1",
            _stance,
            thresholds={"macro_f1": 0.5},
        )
    )

    def _propagation(
        examples: list[dict[str, Any]], _config: dict[str, Any]
    ) -> dict[str, Any]:
        total_p: float = 0
        total_r: float = 0
        count = 0
        for example in examples:
            posts = example.get("input") or []
            expected = set(example.get("gold") or [])
            if not isinstance(posts, list) or not expected:
                continue
            score = evaluate_propagation_edges(posts, expected)
            total_p += score["precision"]
            total_r += score["recall"]
            count += 1
        return {
            "precision": round(total_p / count, 4) if count else 0.0,
            "recall": round(total_r / count, 4) if count else 0.0,
            "f1": (
                round(
                    2 * (total_p / count) * (total_r / count)
                    / ((total_p / count) + (total_r / count)),
                    4,
                )
                if count and (total_p + total_r)
                else 0.0
            ),
            "examples": count,
        }

    registry.register(
        EvaluatorDefinition(
            "propagation_edges",
            "f1",
            _propagation,
            thresholds={"f1": 0.5},
        )
    )

    def _citations(
        examples: list[dict[str, Any]], _config: dict[str, Any]
    ) -> dict[str, Any]:
        total_correctness: float = 0
        total_refusal: float = 0
        count = 0
        for example in examples:
            cards = example.get("input") or []
            known = set(example.get("gold") or [])
            if not isinstance(cards, list):
                continue
            correctness = evaluate_claim_cards(cards, known)
            refusal = evaluate_no_evidence_refusal(cards)
            total_correctness += correctness["citation_correctness"]
            total_refusal += refusal["refusal_rate"]
            count += 1
        return {
            "citation_correctness": round(total_correctness / count, 4) if count else 0.0,
            "no_evidence_refusal_rate": round(total_refusal / count, 4) if count else 0.0,
            "examples": count,
        }

    registry.register(
        EvaluatorDefinition(
            "claim_citations",
            "citation_correctness",
            _citations,
            thresholds={"citation_correctness": 0.98},
        )
    )

    return registry
