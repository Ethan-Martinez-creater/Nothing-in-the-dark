"""重跑领域算法回归（E-4.2）。

对合成标注集调用 ``app.services.evaluation`` 的纯函数，打印阈值表。
任一项低于门槛则退出码 1。不访问数据库、不调用 LLM。

用法（在 backend/ 下）::

    .venv\\Scripts\\python.exe scripts/run_domain_eval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.evaluation import (  # noqa: E402
    evaluate_claim_cards,
    evaluate_no_evidence_refusal,
    evaluate_origin_candidates,
    evaluate_propagation_edges,
    evaluate_report_citations,
    evaluate_sentiment,
    evaluate_stance,
)
from tests.synthetic_eval_dataset import (  # noqa: E402
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


def run() -> dict:
    origin = evaluate_origin_candidates(
        ORIGIN_POSTS, ORIGIN_EDGES, ORIGIN_RELEVANT, ks=(1, 3, 5)
    )
    report_scores = [
        evaluate_report_citations(report, known) for report, known in REPORTS
    ]
    return {
        "sentiment": evaluate_sentiment(SENTIMENT_CORPUS),
        "stance": evaluate_stance(STANCE_CORPUS),
        "propagation": evaluate_propagation_edges(
            PROPAGATION_POSTS, PROPAGATION_EXPECTED
        ),
        "origin": origin,
        "claim_citations": evaluate_claim_cards(CLAIM_CARDS, KNOWN_EVIDENCE_IDS),
        "refusal": evaluate_no_evidence_refusal(REFUSAL_CARDS),
        "report_citations": report_scores,
    }


def _check(name: str, ok: bool, detail: str, failures: list[str]) -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}: {detail}")
    if not ok:
        failures.append(name)


def main() -> int:
    scores = run()
    failures: list[str] = []
    print("Domain algorithm regression")
    _check(
        "sentiment.accuracy",
        scores["sentiment"]["accuracy"] >= 0.9,
        f"{scores['sentiment']['accuracy']} (>= 0.9)",
        failures,
    )
    _check(
        "sentiment.macro_f1",
        scores["sentiment"]["macro_f1"] >= 0.9,
        f"{scores['sentiment']['macro_f1']} (>= 0.9)",
        failures,
    )
    _check(
        "stance.accuracy",
        scores["stance"]["accuracy"] >= 0.9,
        f"{scores['stance']['accuracy']} (>= 0.9)",
        failures,
    )
    _check(
        "stance.macro_f1",
        scores["stance"]["macro_f1"] >= 0.9,
        f"{scores['stance']['macro_f1']} (>= 0.9)",
        failures,
    )
    _check(
        "propagation.precision",
        scores["propagation"]["precision"] == 1.0,
        str(scores["propagation"]["precision"]),
        failures,
    )
    _check(
        "propagation.recall",
        scores["propagation"]["recall"] == 1.0,
        str(scores["propagation"]["recall"]),
        failures,
    )
    _check(
        "origin.P@1",
        scores["origin"]["at_k"]["1"]["precision"] == 1.0,
        str(scores["origin"]["at_k"]["1"]["precision"]),
        failures,
    )
    _check(
        "origin.R@3",
        scores["origin"]["at_k"]["3"]["recall"] == 1.0,
        str(scores["origin"]["at_k"]["3"]["recall"]),
        failures,
    )
    _check(
        "claim.citation_correctness",
        scores["claim_citations"]["citation_correctness"] == 0.75,
        str(scores["claim_citations"]["citation_correctness"]),
        failures,
    )
    _check(
        "refusal.rate",
        scores["refusal"]["refusal_rate"] == round(2 / 3, 4),
        str(scores["refusal"]["refusal_rate"]),
        failures,
    )
    _check(
        "report.coverage[0]",
        scores["report_citations"][0]["citation_coverage"] == round(2 / 3, 4),
        str(scores["report_citations"][0]["citation_coverage"]),
        failures,
    )
    _check(
        "report.coverage[2]",
        scores["report_citations"][2]["citation_coverage"] == 1.0,
        str(scores["report_citations"][2]["citation_coverage"]),
        failures,
    )
    print()
    print(json.dumps(scores, ensure_ascii=False, indent=2))
    if failures:
        print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("\nAll domain regression checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
