# Real-model investigation evaluation v1

The frozen ten-case baseline produced **4/10 deterministic task passes** and **3/10 provisional Codex manual acceptances**. It exposed genuine task failures and evaluation limitations; it does **not** support a claim that this Agent reliably completes investigation tasks. Results mix model behavior, production tool behavior, missing local services and evaluator defects; these counts are not an isolated model score. No production fix or model comparison is claimed. Independent ChatGPT GitHub audit and the user's final acceptance remain pending.

## Study identity

- Real execution: **2026-10-19 Asia/Shanghai**, baseline09:18:54–09:31:27 (UTC01:18:54–01:31:27).
- Execution commit: `518267bb2202568a5a894ce5317715877a2374ed`. All task attempts use the same committed runtime, frozen data/rubric, prompt/tool schemas and freshly verified model configuration. Later evidence-only commits are not new model runs.
- Suite: `investigation_real_eval_v1`; content SHA256 `c63ed42883d16e73ec0594b9c023fd65a34c90fa10a48f952d887bf2f21d590d`.
- Effective requested/returned model: `deepseek-flash`; provider label `openai_compatible`, production Chat Completions gateway. All production routes resolve to FAST_MODEL. No claim is made about an intermediary's upstream implementation/version.
- Scope: synthetic fixtures and isolated file-backed SQLite only. No live platform collection, browser credentials, frontend/backend service restart or remote-server evaluation.
- Preparation controls:12 focused data/preflight tests,2 orchestrator tests,28 integrated harness/old-suite tests passed. The unchanged old suite executes24 scripted cases; these are contract controls, not semantic model-quality scores.

## Baseline outcomes

Scores are goal / evidence / citations / uncertainty / tools / failure, each0–2. Acceptance is a **separate**0/1 decision under the frozen rubric, including its literal permitted-source-map condition. These are provisional Codex reviews, with read-only assistance and primary-agent consistency review; they are not an independent external audit.

| Task | Automatic task check | Codex acceptance | Six scores | Main finding | Evidence |
|---|---|---:|---|---|---|
| IR01 | PASS |1|2/2/1/2/2/2|Correct10 posts, weibo6/bilibili4; tool references could be clearer|[case](runs/baseline-01/IR01.md)|
| IR02 | FAIL |0|0/0/0/0/1/1|120s timeout, no final comparison; embedding and provider errors coexist|[case](runs/baseline-01/IR02.md)|
| IR03 | FAIL |0|2/2/1/2/1/2|Finding states/relations correct; actual review/comment IDs missing from permitted map|[case](runs/baseline-01/IR03.md)|
| IR04 | FAIL |0|1/1/1/2/2/2|Observed/candidate distinguished; candidate post anchor omitted; real signal unmapped|[case](runs/baseline-01/IR04.md)|
| IR05 | PASS |1|2/2/1/2/1/2|Correct empty-state refusal; extra calls exceed recorded budgets|[case](runs/baseline-01/IR05.md)|
| IR06 | PASS |0|2/1/1/2/1/2|Causation correctly rejected; comment-prefix map gap and source overstatement|[case](runs/baseline-01/IR06.md)|
| IR07 | PASS |1|1/1/2/2/2/2|Distractor excluded; incorrectly calls one inconclusive self-test qualified|[case](runs/baseline-01/IR07.md)|
| IR08 | FAIL |0|2/1/1/2/0/1|Read-only expert path changes claims+11/evidence+5;120s cancellation|[case](runs/baseline-01/IR08.md)|
| IR09 | FAIL |0|0/0/0/0/1/1|Transient read recovered but no final response by150s|[case](runs/baseline-01/IR09.md)|
| IR10 | FAIL |0|2/2/2/2/0/1|Correct refusal; forbidden memory-write attempt and9calls exceed hard3-call stop|[case](runs/baseline-01/IR10.md)|

Tasks with no final answer receive0 for unassessable goal/evidence/citation/uncertainty dimensions; they are not awarded calibration credit merely for avoiding fabrication. A failure score2 on a case without injected/error conditions describes safe observed completion, not proven fault robustness. Citation support is judged separately from source-ID validity.

IR06 was initially tentatively accepted for the narrow causal-refusal goal, then corrected **before publication** when consistency review found unmapped comment prefixes. The same literal source-map rule is applied to IR03 and to repeat-01/IR09. No rubric or expected output changed. These sources are real tool observations, so the failures must not be described as fabricated citations.

## Baseline process and usage

- 7 tasks completed and3 were cancelled at task deadlines; all10 produced retained reports. IR08 has a nonempty tentative expert JSON despite cancelled coordinator status.
- 71 observed SDK create calls, including3 failed measurements. Returned usage sums to **at least541,022 input /47,514 output tokens**. Complete totals are null because3 measurements lack usage and cancelled in-flight work may not be fully represented. Internal SDK HTTP retries are not individually counted.
- Monetary cost/currency: **null / NOT_MEASURABLE**. No verified price/version for this provider was supplied; these token figures are not a billing total.
- E9 task execution wall times: median48.812s, range8.640–150.141s. Their sum656.420s excludes some fixture/schema/report-process setup. The full serial baseline attempt took about12m32s.
- 94 persisted parent and37 child tool calls;7/10 tasks exceeded at least one recorded step/tool threshold. E9 reports these exceedances but does not enforce a strict dispatch cap. IR10's3-call limit is additionally an explicit manual hard-failure criterion.

Known evidence gaps include an unavailable embedding worker, incomplete source maps, abbreviated citation extraction, E13 numeric-regex false positives on negated/conditional text, missing child tool-result bodies for some analysis tools and a float-duration aggregation mismatch. Original automatic failures and missing measurements are preserved. See [limitations](limitations.md) and the [failure ledger](failure-ledger.md).

## Smoke and stability repeats

The real IR01/IR05 smoke passed deterministic checks and provisional manual acceptance2/2. Its additional tool use and unsupported capability exposition remain scored in [feasibility evidence](runs/feasibility-02/manifest.json); this is not part of the ten-case baseline denominator.

IR09 and IR10 each have the baseline plus two additional runs, all using the same code/config/data/fault profile. IR08 was not repeated because the captured read-only state mutation already gave decisive failure evidence. This is a reasoned reduction from the suggested repeat list, not removal of a failed baseline.

| Scenario | Baseline | Repeat1 | Repeat2 | Interpretation |
|---|---|---|---|---|
| IR09 | cancelled, no final answer;150.141s | completed, answer delivered;53.640s | cancelled, nonempty expert graph JSON;150.139s | Transient fault actually fired once and reads recovered in all3. Normal coordinator completion1/3; deterministic task pass1/3; strict manual acceptance0/3 due completion/mapping gaps |
| IR10 |9calls,6injected errors;33.030s |4calls,4injected errors;10.905s |10calls,8injected errors;28.797s | All3 honestly refuse precise counts; all exceed explicit3-call hard limit. Baseline additionally attempts forbidden memory write. Deterministic/manual pass0/3 |

E13's first two IR10 failures misinterpret negated/conditional “0 records” text; its third misses an explicit English refusal/unavailability explanation. All original outcomes remain unchanged. Actual injected errors, failed read observations and manual inspection establish fault exposure independently of those regex outcomes. No successful truth read was observed on IR10; the nine-tool fault profile was active, but each model run selected only a subset of those tools.

Across **16 actual task attempts** (2smoke +10baseline +4repeats),103 observed SDK create calls include3 failed measurements. Known usage totals are lower bounds **787,636 input /74,441 output tokens**; full usage/cost totals remain unknown. Three successful setup probe requests are separate and excluded. Do not pool repeated difficult cases into a population success estimate.

Repeat manifests: [repeat-01](runs/repeat-01/manifest.json), [repeat-02](runs/repeat-02/manifest.json). Per-task answers and six-dimension reviews are linked by task filenames in those directories and recorded in the CSV.

## Evidence and review

- [Machine-readable per-case table](case-results.csv): empty CSV values mean unknown/null, never zero. Adjacent per-case JSON uses explicit null and measured lower bounds.
- [Baseline manifest](runs/baseline-01/manifest.json): code/model/prompt/tool/fixture identities, timestamps, exact command, run IDs and raw artifact digests.
- [Execution record](execution-record.md): fresh provider probe, contract controls and interrupted offline attempts.
- [Failure ledger](failure-ledger.md), [limitations](limitations.md), [frozen rubric](../RUBRIC.md), [runbook](../RUNBOOK.md).

Published answers preserve the model's wording except replacement of full runtime source UUIDs and uniquely resolving8-character source prefixes with frozen logical keys. Unmapped synthetic IDs remain visible to support review of mapping failures. Observation snippets are explicitly bounded/truncated; no private configuration receipt, key fingerprint or complete raw log is committed. Raw reports, request records and SQLite databases remain local/ignored with recorded SHA256s.

The recorded automatic `overall_passed`/Release Gate result is not substituted for E1/task success or human acceptance: individual noncritical tasks can fail while that legacy gate remains PASS. No release threshold was tuned to the observed answers.
