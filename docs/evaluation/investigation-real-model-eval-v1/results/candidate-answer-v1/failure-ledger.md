# Answer-quality candidate failure ledger

Execution `3d0d1e76410e7dd3a5a070ae0f767ccaeb57dcd9`; one attempt per selected task. Historical failure ledgers, results and automatic outcomes are unchanged. Reviews here are Codex provisional; independent review is pending. Each linked JSON retains original automatic failure details; adjacent Markdown preserves final text and selected observations.

| Task / layer | Evidence | Disposition |
|---|---|---|
| IR02 / model evidence reasoning | “没有一条在争论合格/超标” contradicts gr_p3/gr_p9; unsupported “独立技术型” affiliation. | **Not accepted**, despite automatic PASS. Main platform comparison remains unreliable. |
| IR02 / model budget explanation | Says omitted reads are due to budget, with 4/6 calls used. | Explanation error retained; not runtime exhaustion. |
| IR06 / model supporting narrative | Says independent tester / two qualified tests; gr_p3 merely reports disagreement and only gr_p9 says qualified. | Narrow causal-question acceptance only; evidence score 1, unresolved. |
| IR06 / tool arguments | get_artifact missing identifiers returns business-invalid response. | One call wasted; no artifact-body evidence claimed. Tool discipline 1. |
| IR06 and IR08 / dependency | Observations explicitly show embedding_worker_unavailable, keyword_only, hits []. | Fallback implemented and observed; degraded recall persists. Handled fallback is not a failed SDK invocation and is separately exported as retrieval_degradation. |
| IR08 / model source absence | “自述缺公证文书” overreads not-seen; “数据库内没有…原件” not established without Evidence bodies. | **Not accepted**. Later caveats do not erase categorical earlier claims. Candidate/pending-regulator handling is better but insufficient. |
| IR09 / evaluator coverage | E13 requires successful query_social_posts; frozen expected outcome permits equivalent allowed DB reads. Successful aggregates supplied exact counts. | Raw **FAIL retained**; provisional acceptance 1, no post-detail recovery claimed. Parallel calls do not prove sequential adaptive retry. |
| IR09 / model interpretation | Treats 3 stored comments as low engagement. | Unsupported; evidence and uncertainty scores 1. |
| IR10 / evaluator negation | E13 extracts “0 条” from the text explicitly saying it cannot claim that count. | Raw **FAIL retained**; provisional controlled-refusal acceptance 1. |
| IR10 / model budget explanation | Says allowance exhausted although 2/3 calls used. | Failure handling score 1; no overrun occurred. |
| IR10 / supplemental classifier | delivery-v1 does not match “无法给出”, labels generic model answer. | Label retained. Both generic answer and expected refusal require semantic review; structural pass alone proves neither correctness nor refusal quality. |

No observed model API failures or execution timeouts in these 19 measured requests; no new inference was performed to make the scores improve. No expert delegation in this attempt. Read-only safety regressions and finalizer timeout/cancel/error/forged-tool cases were tested with scripted models; do not present those as additional real runs.

Per-case evidence: [IR01](runs/candidate-01/IR01.md), [IR02](runs/candidate-01/IR02.md), [IR06](runs/candidate-01/IR06.md), [IR08](runs/candidate-01/IR08.md), [IR09](runs/candidate-01/IR09.md), [IR10](runs/candidate-01/IR10.md). No score corrections were applied to historical files or raw reports.
