# Bounded answer-quality candidate

Tracked in [Issue #5](https://github.com/Ethan-Martinez-creater/Nothing-in-the-dark/issues/5). Execution commit: `3d0d1e76410e7dd3a5a070ae0f767ccaeb57dcd9`, based on merged PR #4/main `3574d7273151997780bab71e9efb27252e2825fb`. [PLAN.md](PLAN.md) was committed before inference. This is a six-task targeted iteration, not a new ten-task baseline or a general capability benchmark.

## Results and what they mean

| Evidence set | Tasks | Automatic checks | Codex provisional acceptance |
|---|---|---:|---:|
| Original frozen baseline | IR01–IR10 | **4/10** | **3/10** |
| Previous closure candidate | IR01/02/08/09/10 | **5/5** | **2/5** |
| This answer-quality candidate | IR01/02/06/08/09/10 | **4/6** | **4/6** |
| This candidate, same five closure tasks only | IR01/02/08/09/10 | **3/5** | **3/5** |

Automatic success does not imply completion of the investigation objective. Manual acceptance is provisional, based on the unchanged frozen rubric, and awaits independent GitHub audit. The six structural delivery passes only show that reviewable model text was returned; they are not six correct investigations. Different denominators must not be combined into a single improvement rate. There is no statistical or independent holdout claim.

| Task | Auto | Provisional | Actual result and important limit |
|---|---|---|---|
| IR01 | PASS | 1 | Correct persisted count 10, weibo 6, Bilibili 4, in one overview call. |
| IR02 | PASS | 0 | Delivers platform comparison, but falsely says no Bilibili post disputes qualified/exceeded conclusions and overstates creator independence. |
| IR06 | PASS | 1 | Correctly declines additive-to-disease causality from read materials; tool-free synthesis succeeds after 5/5 calls. Supporting narrative still overstates two qualified tests and tester independence. Acceptance is limited to the causal question. |
| IR08 | PASS | 0 | Presents both sides, candidate status and pending regulator response; overstates missing source documents without reading Evidence bodies. |
| IR09 | FAIL E13 | 1 | Discloses transient post-read failure; allowed database aggregates supply accurate counts/trend. No successful post-detail retry. Checker does not recognize this rubric-permitted alternate read. Low engagement inferred from 3 persisted comments is unsupported. |
| IR10 | FAIL E13 | 1 | Correct refusal after two persistent faults; no invented counts or write attempt. E13 extracts a negated “0 条” quotation. Model incorrectly says 2/3 calls exhausted the budget. |

Full answers (source IDs substituted with stable logical keys), six-dimensional scores, tool calls, selected observations, raw digests and failure details are in [runs/candidate-01](runs/candidate-01), [case-results.csv](case-results.csv) and [failure-ledger.md](failure-ledger.md). Raw outputs and raw automatic failures are retained; neither E13 failure was relabeled as automatic PASS.

## Changes under test

- At read-only shared tool-budget exhaustion, successful observations permit one tool-free synthesis operation per run, with a 15-second deadline. Failed/empty/tool-requesting synthesis falls back; all-failed reads skip synthesis; cancellation propagates. Existing shared reservations and write protection remain enforced. This is not an exactly-once provider-delivery guarantee across process crashes.
- Model instructions show remaining allowance, accept expert text for read-only work, and distinguish self-report, independent verification, candidate conclusions and unknown source coverage.
- Only explicit `embedding_worker_unavailable` degrades to the existing scoped keyword search. Responses disclose mode/degradation. Other failures propagate. This preserves availability, not semantic recall.
- Supplementary `answer-delivery-v1` separates runtime stops from reviewable model text. Semantic acceptance remains manual. Diagnostics distinguish cancelled model requests from provider failures.

IR06 is the only real task that exhausted its allowance and used the new synthesis path. IR02/08/09 returned ordinary model final answers before allowance exhaustion, so their change cannot be attributed specifically to the finalizer. None of the six delegated to an expert; shared child and write boundaries are covered by scripted tests, not same-path real-model reproduction.

## Execution and integrity

One serial attempt per preselected task on 2026-10-19, 13:30:01–13:34:11 UTC (21:30–21:34 Shanghai). Actual model: `deepseek-flash`. The user supplied fresh private configuration; its verified receipt was still valid at execution. **19 observed SDK create requests, all succeeded; 127,621 input and 16,571 output tokens, measured for all requests.** These include helper requests, not just coordinator turns. **21 actual tool invocations**, including failed calls. Monetary cost is unknown (`null`), not zero. Summed measured task latency is 134,639 ms; wrapper wall time includes preparation/overhead and is not that sum.

IR06 and IR08 both recorded unavailable embeddings, keyword-only mode and empty hits; service restoration or equivalent semantic recall is not claimed. The model API succeeded in all observed requests in this attempt; this does not remove historical API errors/timeouts. No new retry sweep or post-result model run was performed.

[integrity.json](integrity.json) records 81 preexisting tracked fixture/rubric/evidence files unchanged against main (Git LF-normalized contents), all six before/after case snapshots equal, all original report digests matching the attempt index, and 18 published report/trace/request digests verified. Raw reports, requests, traces and task databases remain in the ignored local artifact directory named in each record; public excerpts are not a complete raw archive. Private credentials and absolute local Windows paths are excluded.

## Validation and reproduction

From `backend`, with Python/dependencies provisioned as in the project runbook (PowerShell):

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m pytest tests/test_budget_answer_finalization.py tests/test_execution_policy_runtime.py tests/test_execution_policy_api.py tests/test_investigation_diagnostics.py tests/test_answer_delivery.py tests/test_evidence_retrieval_degradation.py tests/test_search_evidence_fallback.py tests/test_agent_runtime.py tests/test_agent_loop.py tests/test_agent_eval_real_source_evaluators.py tests/test_investigation_preflight.py -q -o addopts= -p no:cacheprovider --basetemp=artifacts/pytest-answer-integrated-02
python -B -m pytest tests/test_agent_eval_contract.py -q -o addopts= -p no:cacheprovider --basetemp=artifacts/pytest-answer-compat-01
```

Observed **102 passed** (20.58s) and **22 passed** (138.49s, includes existing 24 scripted scenarios). Choose a fresh basetemp path on repeat; do not delete retained temporary evidence. An initial command named a nonexistent test file; it collected nothing and was corrected, not counted as a test pass or model failure. Targeted changed-file Ruff checks and `git diff --check` passed. CI includes new delivery/diagnostics/finalization/retrieval tests and requires no real keys; CI status is recorded in the PR.

For reproducing this paid experiment, first check out execution commit `3d0d1e7`, configure `backend/.env` with fresh `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_PROVIDER=openai_compatible`, `LLM_FAST_MODEL`, `LLM_REASONING_MODEL`, `LLM_REPORT_MODEL`, and follow [the frozen runbook](../../RUNBOOK.md) for a valid preflight receipt. All actual routes currently use FAST_MODEL; do not assume three different models were exercised. Use a fresh output directory and valid receipt rather than overwriting this attempt:

```powershell
python -B -m app.scripts.run_investigation_study --tasks IR01,IR02,IR06,IR08,IR09,IR10 --attempt-label candidate-answer-v1 --config-file .env --preflight-receipt artifacts/agent_eval/real_v1/preflight-01.json --output-dir artifacts/agent_eval/answer_quality_v1/reproduction-01
```

This is a reproduction instruction, not authorization to silently repeat the paid experiment. Exact original per-case CLI, prompt/tool-schema/config metadata, execution SHA and frozen hashes are in [manifest.json](runs/candidate-01/manifest.json). The private config/receipt paths there refer to retained local worktrees and are not supplied to CI.

## Remaining boundaries

Source overstatement, missing-Evidence coverage and unreliable explanations of remaining budget remain real model problems. Keyword fallback cannot compensate for unavailable embeddings. E13 still has alternate-read and negation limitations; delivery-v1 also misses the phrase “无法给出” in IR10, leaving it as generic reviewable model text rather than expected refusal. We retain the original labels and document the counterexamples rather than alter the frozen evaluator or chase a score.

These are synthetic frozen database investigations, not fresh platform collection or legal/clinical verification. No real expert delegation occurred here, no production deployment was modified, and no broad non-policy GraphWorker budget redesign or PostgreSQL contention validation was attempted. Prompt changes are task-informed, the sample is small and non-independent, and model variation remains possible. The project can demonstrate bounded statistics, scoped analysis and fault refusal; it cannot claim consistently reliable autonomous investigation or that all discovered quality problems are fixed.
