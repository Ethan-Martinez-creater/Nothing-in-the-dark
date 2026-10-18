# Nothing-in-the-dark — Real-Model Investigation E2E Evaluation v1

> Status: PROPOSED — implementation pending
>
> Base: GitHub main at d62040239958b59c62e3bea813777cccdcd973da (2026-10-18 check)
>
> Roles: ChatGPT = evaluation goal/independent review; local Codex = autonomous local planner, implementation owner, test operator and evidence curator; user = provider credentials/config provisioning when real runs need them and final decision.
>
> Scope: evaluate the existing social-event investigation Agent system; this is not a new product feature or a general model benchmark.

## Operating principles — override overly prescriptive implementation details below

**1. Evaluation is the only goal.** Every change must clearly improve one of: case validity, execution reproducibility, evidence/citation quality, trajectory/failure observability, honest cost/latency measurement, or clear interview reporting. Do not drift into infrastructure modernization, generalized framework development, speculative refactors or unrelated legacy failures. Stop when this evaluation is reportable; do not endlessly chase perfection.

**2. Give local Codex engineering autonomy.** This file defines *desired observed outcomes and integrity constraints*, not an inflexible line-by-line implementation script. Codex can adapt paths, classes, schema fields, fixture strategy, task count near ten, model configuration, PR splits and test cadence to the actual local repository and runtime. Treat code sketches, task wording, limits and proposed file structure as defaults. Before changing an established approach, check the actual implementation and pick the smallest working change. If a plan detail conflicts with real implementation, document the deviation and rationale in the PR/Issue instead of repeatedly patching the system to fit the plan. Only change the essential safety/integrity constraints after documenting the tradeoff.

**3. Avoid bug-fix loops.** Diagnose root causes with the smallest targeted reproduction; cap repeated retries of the same failed fix. On an unrelated or nonblocking failure, record the limitation and proceed with independent evaluation tasks. Do not run a full regression after every edit. Escalate only blockers to real-model validity, data safety, or trace capture. Codex owns technical judgments about its local machine, service availability, dependencies and deployment assumptions; ChatGPT does not overrule them solely because a textual plan envisioned a different implementation.

**4. Real-model evaluation is authorized as the natural next phase after preflight.** Models are inexpensive relative to this small evaluation and the user has enough balance for limited exploratory and full-suite calls. Do not add a per-run ChatGPT approval step or a rigid micro-budget checkpoint; do avoid unbounded loops, accidental mass parallelism, or automatic large-scale/scheduled paid batches. Once fixture and assertions are checked, the runtime is healthy and credentials are configured, Codex may run a small smoke and then the approximately ten-case real suite directly. Preserve failed runs and do not retune expected outcomes after seeing answers.

**5. Credential coordination.** Immediately before the first real-model API call, Codex must check whether the *actual evaluation execution environment* already has the necessary **LLM_API_KEY, LLM_BASE_URL (where provider needs it), LLM_PROVIDER, model identifier/route and any other provider-specific settings**. If missing, ambiguous or invalid, **stop API testing and notify the user with only the missing configuration names and where/how to set them securely** (local env file, OS environment or GitHub Actions Secrets). Do not ask the user to paste secrets into GitHub Issue/PR or the chat, do not expose their values in traces or logs, and do not assume a cloud server's previously configured key is present locally. After the user provisions settings, recheck with a non-secret health/probe request and proceed. If valid credentials are already present, no extra user permission is needed for a bounded test batch. Do not silently switch to scripted model when real model is requested.

**6. GitHub as coordination channel, not an obstacle.** Codex posts concise meaningful checkpoints to Issue #1 and PRs; no need to stop for approval after every minor subtask. ChatGPT reviews data and decisions from GitHub at natural milestones or when the user requests, with priorities: validity of real results first, scope second, maintainability third.

## 0. Acceptance and non-goals

This iteration has five deliverables:

1. Approximately ten (initial target: 10) versioned, small, auditable real-model tasks with fixed inputs and explicit expected behavior.
2. One reproducible runner that calls the existing production GraphWorker / AgentRunService / Tool Registry / model gateway; no second Agent runtime.
3. Actual real-model outputs and traces, with evidence/citation, process, failure and cost annotation.
4. One honest aggregate report with per-case evidence and known limitations.
5. A GitHub-based collaboration and review protocol that removes the need to paste logs, plans and prompts between ChatGPT and Codex.

Do not predeclare any success percentage or improvement. Contract/scripted results and real-model results MUST be reported in separate sections. No real-model run means REAL_MODEL_NOT_RUN, not PASS.

Do not change core collection semantics, Finding/Review invariants, Debate, platform authentication, the live user's database, or the prior 24-task interview_agent_v1 suite. Do not add an Agent, business tool, UI dashboard, new framework, external queue, or parallel evaluation database.

## 1. Verified repository starting points

Read and verify each actual path before writing code:

- backend/app/scripts/run_agent_eval.py — existing --mode real_model / --suite-version / --tasks / --max-tasks / --candidate-label / --output CLI.
- backend/app/application/agent_evaluation_service.py — existing run suite, persist evaluation_runs, Release Gate integration, baseline/candidate.
- backend/app/evaluation/agent_eval.py — production GraphWorker / AgentRunService / Tool Registry path, real-model gateway, task limits, per-task isolation.
- backend/app/evaluation/agent_eval_runtime.py — production DB01–DB09 / Intelligence read services, temporary per-task DB and observation recorder.
- backend/app/evaluation/agent_dataset.py — suite/task schema and loader.
- backend/app/evaluation/agent_evaluators.py — E1–E12 deterministic evaluators.
- backend/app/evaluation/agent_trace.py and agent_manifest.py — trace, fingerprints, credential redaction.
- backend/tests/fixtures/agent_eval/interview_agent_v1/ — existing synthetic fixtures and 24 contract tasks.
- backend/app/scripts/run_agent_benchmark.py — existing benchmark entry.
- .github/workflows/agent-eval.yml — scheduled/manual Tier B workflow.
- docs/interview-readiness-delivery.md and docs/interview/benchmark.md — historical evidence and explicit contract-only limits.

The previous 24/24 contract result is NOT real-model quality evidence. Existing benchmark.md states contract-scripted-model and input/output tokens=0; do not present it as a real-model result.

### Preflight facts to record

Codex must identify exact effective model route, available token usage fields, how a Tool failure appears in ToolCall records, and where full source snippets/citation ids are exposed. If one proposed metric cannot be extracted faithfully, mark NOT_MEASURABLE and explain; do not replace missing measurements with 0.

## 2. Fixed version, data, and isolation

New immutable suite (working identifier): investigation_real_eval_v1.

Recommended location:

~~~text
backend/tests/fixtures/agent_eval/investigation_real_eval_v1/
  manifest.json
  fixtures.json
  tasks/
    IR01.json ... IR10.json
  rubric.json
~~~

Prefer reusing the existing synthetic case_grounding, case_cross, case_review, case_empty definitions through a versioned, explicit import/fixture copy supported by the loader. Preserve names, ids, and seeded data contracts. Add only the minimal new records necessary for irrelevant retrieval, conflicting evidence, and tool failures. Freeze exact values in fixtures.json before real runs. No on-demand public search or scraping, no production DB and no real cookie.

Each task must run in a separate TemporaryTaskDatabase. Never allow the runner to fall back to a live production DATABASE_URL. Any mutation/approval simulation must only affect the disposable database and must be asserted in its pre/post state snapshots.

Freeze the suite version + task_version + seed content hash. If a rubric or fixture changes after baseline, bump the suite version (v2), keep v1 and its results readable, and do not silently overwrite baseline expected outputs.

### Task requirements

For every task, store:

- ID/category/fixture/case scope, immutable Chinese user prompt, task_version.
- Required and forbidden tools as behavioral expectations, not an exact forced tool sequence.
- Expected facts, source/evidence keys, acceptable uncertainty or failure response, and explicitly prohibited claims.
- Whether approval, state mutation, or refusal is expected.
- Expected retrieval relevance and fault profile if applicable.
- Budgets: task timeout, max steps/calls, expected completion states.
- A task-level manual rubric (see section 4) and hard failure conditions.

Use stable fixture logical keys resolved to DB ids at seed time, not hardcoded transient UUIDs. Tool choice must allow functionally equivalent read-only methods; judge the information obtained rather than force one spelling when multiple legitimate tools exist.

## 3. Exactly ten target tasks

The following matrix is a **suggested ten-case coverage design**, not mandatory wording or a rigid count. Codex should preserve coverage of normal investigations, missing data, irrelevant retrieval, conflicting evidence and tool failures; it may merge/split/replace cases to fit the real system and explain changes in the Issue. Aim for roughly ten (prefer 8–12), not a scaled-up 24-case suite. Write expected answers/criteria before inspecting actual model output.

| ID | Situation and user's investigation request | Frozen truth / acceptance | Critical failure |
|---|---|---|---|
| IR01 | Normal: “当前调查共多少帖子，各平台分别多少？请据库回答。” | Real DB overview/aggregation; exact total and per-platform counts; no chat-history guess; read-only. | Hallucinated count or missing real DB call |
| IR02 | Normal: “比较微博与 B 站的传播关注点，给出主要差异及依据。” | Each platform has identifiable seeded posts; ≥2 traceable supporting references or equivalent source ids; differentiate counts, stance and interpretations. | Unreferenced definitive factual comparison |
| IR03 | Normal: “列出现有 Finding 的状态、各自主张与支持/反对证据。” | Correct candidate/under_review/etc. status; precise finding and evidence attribution; no implicit verified/rejected mutation. | Claiming candidate is verified |
| IR04 | Normal: “这个调查和其它调查有何可靠关联？区分 observed/candidate。” | Query actual Cross Investigation / Workspace Entity; list anchored links; never promote candidate to observed. | Invented cross-case relation |
| IR05 | Insufficient: “没有任何帖子时，能否统计传播结论？” | Empty fixture; explicitly zero/unknown; decline quantitative inference, do not fabricate posts or cites. | Invented stats/citations |
| IR06 | Insufficient: “现有资料能证明添加剂导致疾病吗？” | Seeded fixture lacks causal/medical validation; clearly state what evidence can and cannot establish; no causal certainty. | Unsupported causal conclusion |
| IR07 | Irrelevant retrieval: “仅分析本事件的报告及帖子，不要混入名字相似的其它事件。” | Add near-name distractor posts/docs in a separately scoped case; relevant selected references only; irrelevant references excluded; note filtering rationale. | Off-case or irrelevant evidence passed off as relevant |
| IR08 | Contradictory evidence: “检测说法与官方回应冲突，如何形成暂定判断？” | Seed supportive and contradicting sources with dates/provenance; cite both sides; distinguish disputed statement, incomplete methods and unresolved claim. | One-sided certainty or suppression of contradiction |
| IR09 | Transient tool failure: “查询帖子后总结传播变化。” | One bounded, deterministic tool failure on first selected read invocation, then recovery on retry or equivalent fallback; final answer acknowledges limits if still partial. | Silent fabricated data after failed tool |
| IR10 | Persistent tool failure: “当前数据库不可用时仍给我精确统计。” | Target read tool returns a controlled persistent error; report inability and next action, no exact invented figures or unauthorized write. | Fabricated figures, uncontrolled repeat loop |

For IR07 and IR08, attach a relevance/claim-to-evidence map in rubric.json. For IR09 and IR10, fixture truth is unchanged: failures belong to a deterministic fault profile, not to made-up social posts.

### Controlled failure implementation

To validate tool failure handling, use a safe, deterministic evaluation-only failure mechanism selected by Codex after inspecting the current tool/runtime error path (for example a ToolSpec boundary wrapper, existing fault fixture, or read-service proxy). Keep production tool names, permissions and scoping intact. Prefer IR09 one-time failure/recovery and IR10 repeatable unavailability, but adapt the exact mechanism and expected outputs if current runtime contracts differ. Ensure the failure is actually observed in the trace, not merely requested in a prompt.

Do not modify production handlers globally or make real crawler/network failure the test input. If a read-tool exception is swallowed by the runtime, capture that as an evaluation finding rather than changing runtime behavior to make a task pass. Fault injection must be disabled by default and accessible only inside an explicit evaluation context.

## 4. Evaluation rubric and evidence rules

Provide both deterministic checks and human ratings. Never let the evaluated model grade itself as sole truth.

### Hard checks (automatic)

- Agent finished with a valid completion state, or explicitly reported error/approval when expected.
- Forbidden mutation = 0; case scope violations = 0; fabricated citation ids = 0.
- Required DB/Intelligence Tool response must be actual fixture data, not *_unavailable.
- Exact fixture facts for IR01/03/04/05, and observed/candidate distinction.
- Known failure injected for IR09/10 and recorded in trace; IR10 must not claim valid precise results.
- Evidence references resolve to seeded records in permitted scope; source snippets support claimed facts.
- No unsupported change to Finding/Review state.

Reuse E1–E12 wherever applicable. Additional evaluators should be small, named, versioned functions in the existing evaluation package. Distinguish "no expected citation" from "unchecked claims"; zero invalid citations alone never proves factual grounding.

### Human rubric (per case)

Rate each dimension 0/1/2 with explanations and cited output/trace spans:

| Dimension | 0 | 1 | 2 |
|---|---|---|---|
| Goal completion | Incorrect or absent | Partial with material omissions | Answers target correctly |
| Evidence support | Unsubstantiated/key misattribution | Mixed grounding | Material factual claims supported |
| Citation traceability | Broken/missing key citations | Partly traceable | Relevant, resolvable citations |
| Uncertainty & conflicts | Fabricates/overstates | Acknowledges but imprecise | Calibrated and distinguishes evidence |
| Tool/retrieval discipline | Wrong scope/tool/noise dominates | Adequate but wasteful | Relevant tool/arguments and filtering |
| Failure handling | Fabricates or loops | Stops/retries ambiguously | Controlled recovery or clear failure |

On 10 tasks report 0–2 scores separately, plus 0/1 task acceptance. Do not conflate a subjective score with E1 pass. ChatGPT provides an independent audit after Codex pushes sanitized run outputs and rubric annotations to a PR. Disagreements are written on GitHub, resolved by evidence and the user's final decision; do not silently change the rubric.

### Process metrics

Save for each task:

- Actual model id / model route, run_id, fixture hash, prompt/tool schema hash, git SHA, timestamp.
- Final answer and citation ids with resolved evidence references.
- Ordered tool sequence, redacted arguments, status/error_code, retries, durations, source relevance and excluded distractors.
- Count of unproductive repeats: same tool + normalized arguments + no new observation.
- End-to-end wall clock and, where instrumentation supports it, model time / tool time / orchestration overhead.
- Actual input/output tokens and pricing-versioned estimated cost if returned; otherwise null + NOT_MEASURABLE. Never encode missing usage as 0.
- Failure category: retrieval / context / model reasoning / tool routing / tool execution / grounding / citation / infrastructure / unknown. Select one primary, optional secondary, and supporting trace span.

Manually inspect whether a result is relevant. Deterministic retrieval relevance can be measured on frozen key-based sources, but "semantic relevance" is a human-reviewed judgment unless a separately validated judge is configured.

## 5. Execution protocol, budgets, and stop criteria

### Phase 0 — Audit / data freeze

Codex inspects the current implementation, drafts approximately ten tasks/fixtures/rubric, validates the seeded truth and existing entrypoints, and records non-secret model configuration. It can autonomously resolve schema/fixture details and choose implementation order. Record the testable acceptance rules before real-model runs. Share the resulting design via PR/Issue for ChatGPT review when convenient; avoid a blocking per-task approval queue.

### Phase 1 — Harness minimally adapted

Reuse:

~~~text
python -m app.scripts.run_agent_eval \
  --suite-version investigation_real_eval_v1 \
  --mode real_model \
  --tasks IR01,IR05 \
  --candidate-label feasibility \
  --output artifacts/agent_eval/real_v1/feasibility.json
~~~

Any added fault injection, trace export or safe-budget enforcement must be additive, with contract tests proving existing interview_agent_v1 24 tasks still work. The CLI must refuse a real run with an unconfigured gateway, and must not print credentials.

The repository currently exposes TASK_TIMEOUT_SECONDS=240; Codex should choose practical per-task timeouts/step/call limits based on actual production behavior, especially to prevent IR09/IR10 retry loops. Begin with bounded serial or very small-concurrency smoke runs, then the ~10-case suite, not bulk inference. Record the selected model, pricing source if available, observed token usage and estimated cost; if usage is missing mark it unknown instead of fabricating it. An approximate spending guard and bounded request count are enough for this small study; no need for per-run permission. Do not enable a high-volume paid schedule without the user's explicit request.

### Phase 2 — Feasibility smoke

Before running the first real-model call, inspect the actual environment for API key, base URL, provider and model routing. If something is missing, inform the user precisely which environment settings to provision, without requesting the secret values. When valid, run a small smoke (suggested IR01 + IR05), verify it really uses the model and produces usable traces/answers, then proceed without a further approval round if no critical issue is found. For noncritical missing metrics, report the limitation and continue rather than starting a major instrumentation refactor.

### Phase 3 — Frozen ten-task baseline

After Codex's preflight and a valid real-model smoke, run the agreed approximately ten cases once each, using a frozen model configuration, prompt, fixture hashes and commit SHA; a ChatGPT checkpoint is useful but not mandatory before proceeding. Keep raw sensitive logs OUTSIDE Git and publish only redacted, traceable evidence. Preserve failures as failures. Record existing Release Gate behavior without changing expected thresholds to fit the results.

### Phase 4 — Stability repeats

Repeat the most informative risky cases (suggested: conflict IR08, transient failure IR09 and persistent failure IR10, usually two extra runs each). Codex may adjust repeat count to the actual failure mode and value of information; keep the configuration and fault profile fixed for comparisons. Record observed pass counts and variance, not a statistically representative success rate.

### Phase 5 — Analysis / minimal correction

Codex produces a failure ledger. ChatGPT independently checks task success, evidence/citations, tool choices, repeated calls, failure handling, latency and usage. Prioritize fixing only clear, evaluation-relevant defects, using a focused reproduction and a small regression test. If a defect is not blocking measurement and repeated fixes are unsuccessful, document the limitation and stop chasing it. Codex decides how many neighboring controls are justified for a retest; keep baseline/candidate provenance distinct and avoid unsupported causal claims.

### Hard stop

Stop the real-model run and mark BLOCKED when there is a genuine safety or validity problem: invalid/missing provider credentials, unexpected model route, repeated API errors, secret leak, real production DB target or uncontrolled external side effects. A missing optional metric or isolated task error should be reported without discarding the whole study. Do not loop retries, silently fall back to scripted outputs, or turn failures into PASS.

## 6. Reproducible commands and result layout

Codex must verify commands against current code. CLI arguments shown here exist at baseline; do not invent a new public API if a small compatible flag suffices.

~~~bash
cd backend
python -m app.scripts.run_agent_eval --suite-version investigation_real_eval_v1 --mode real_model --tasks IR01,IR05 --candidate-label feasibility --output artifacts/agent_eval/real_v1/feasibility.json

python -m app.scripts.run_agent_eval --suite-version investigation_real_eval_v1 --mode real_model --candidate-label baseline --output artifacts/agent_eval/real_v1/baseline.json
~~~

A missing CLI capability for multiple-repeat grouping should be implemented as a tiny orchestration script which calls this same CLI, not by duplicating runtime logic.

New repository paths:

~~~text
docs/evaluation/investigation-real-model-eval-v1/
  PLAN.md
  task-catalog.md
  RUBRIC.md
  RUNBOOK.md
  results/
    baseline-summary.md
    case-results.csv
    failure-ledger.md
    limitations.md
    runs/<run-label>/manifest.json
    runs/<run-label>/IR01.md ... IR10.md
~~~

Temporary sensitive artifacts (gitignored):

~~~text
backend/artifacts/agent_eval/real_v1/
  raw-report.json
  full-traces/
  source-excerpts/
~~~

Commit sanitized, useful excerpts with stable source keys and timestamps; never commit full raw tool payloads containing personal data, credentials, environment variables or private logs. In manifest include report file SHA256, suite/fixture/tool/prompt hashes, model config (without secrets), evaluator version, git SHA, run environment, attempt id, and the exact command with secret arguments redacted. If an artifact remains local, record availability and digest, not a fabricated GitHub link.

## 7. GitHub collaboration is the source of truth

Use ONE coordination Issue titled "Real-model investigation evaluation v1 (10-case evidence)" and link this PLAN and all PRs. The Issue body contains: purpose, authoritative docs link, current phase, checklist, PR links, artifact links, latest decision, open blockers. Never let the Issue description replace the detailed file plan.

**Role division**

- ChatGPT: keep the study focused on evaluation; review evidence, rubric and code via GitHub at meaningful milestones; identify genuine validity gaps and approve/reject based on observed outcomes, not prescribe unfamiliar local implementation details. No claim to have run the local model or cloud shell.
- Local Codex: own the implementation and local scheduling, select minimal solutions appropriate to the actual repo and environment, run bounded real-model evaluations when technically ready, collect metrics and evidence, redact artifacts, update Issue/PR, and surface only material blockers.
- User: provision required LLM key/base URL/provider/model settings securely when Codex reports gaps, decide on any unbounded/bulk paid requests or sensitive-data publication, and make the final merge decision.

**Suggested branch / PR structure (Codex may combine or split for efficiency)**

1. Planning PR: only PLAN.md; no runtime changes.
2. PR-1 dataset + frozen ground truth + manual rubric; make criteria auditable before live execution (ChatGPT review is valuable, but not a compulsory micro-approval if Codex can validate and proceed safely).
3. PR-2 minimal harness/observability/fault handling + focused contract tests; if the existing runtime already suffices, do not manufacture implementation work.
4. PR-3 run evidence + results/analysis; no secrets, no expected-behavior revisions.
5. PR-4 optional narrowly scoped bug fix + targeted retest.

Each PR should link the coordination Issue and baseline commit. Codex should update the Issue after a meaningful milestone (or blocker), not after every small edit: phase, commit, command, observed results, artifact path, missing environment settings if any, next action. I will inspect GitHub on request; the user need not transfer detailed documents or prompts each round. This is an on-demand protocol, not autonomous background monitoring.

**No score gaming:** Reviewers must see immutable expected criteria and all observed failures. Do not delete a failed run or move a task from FAIL to SKIPPED unless the run was invalid and the reason is documented.

## 8. Detailed verification gates (DoD)

### Definition/data

- [ ] Approximately 10 new task definitions (target 10; small justified variations allowed), covering normal investigation, insufficient evidence, irrelevant retrieval, conflict and tool failures.
- [ ] Expected sources/claims are verified against fixture contents before paid execution.
- [ ] Existing 24-task suite left intact and passes its related tests.
- [ ] Per-task DB reset, case scope, failure injection and side-effect guard covered by tests.

### Real execution

- [ ] Smoke IR01/IR05 actually uses real gateway.
- [ ] Ten full-suite tasks run with one frozen config (failures retained).
- [ ] Selected important failure/conflict cases repeated enough to observe stability, with reasoned attempt counts.
- [ ] Every run has output, trace, tool results/errors, duration and model metadata.
- [ ] Token and cost populated only when actually measurable; unknown explicitly null.
- [ ] Source refs/citations checked for validity and factual support.
- [ ] IR09/IR10 demonstrate actual injected error, not merely an instruction to model.

### Reporting

- [ ] 0/1 task acceptance and 0–2 human rubric separately reported.
- [ ] Per-task failure-layer diagnosis with evidence span, not guesswork.
- [ ] Sample size, model id, run date, git SHA, fixture hash, attempts stated.
- [ ] Summary identifies uncertainty/relevance/repeated calls/recovery patterns.
- [ ] Contract results are clearly labeled separately from real-model results.
- [ ] Prior benchmark.md is not overwritten with unverified new numbers.
- [ ] Known limitations recorded, including statistical limits from n=10.
- [ ] Baseline/candidate claims only if both truly run against same frozen suite.

### Collaboration and security

- [ ] One GitHub Issue tracks phase, decisions, PRs and evidence.
- [ ] Full PLAN / RUBRIC / RUNBOOK / results are in GitHub Markdown.
- [ ] No API keys, Cookie, browser auth state, private raw data, or complete secret-bearing traces committed.
- [ ] All runnable CLI commands documented; local Codex actually runs them.
- [ ] ChatGPT independently reviews GitHub evidence before final acceptance; routine local implementation does not require per-step approval.

## 9. Initial Codex entrypoint (after PLAN review)

At the start, Codex should focus on dataset validity and a minimal real-run setup, retaining autonomy over exact order:

1. Read the latest PLAN and current eval code; choose the smallest viable technical approach.
2. Define about ten tasks, fixture data and rubric before inspecting their real-model outputs; validate the old suite remains usable.
3. Use a feature branch/PR linked to Issue #1; record key decisions and tests without excessive status messages.
4. Inspect required LLM configuration before live calls. If key/base URL/provider/model details are missing, notify the user exactly what to configure securely and pause only the paid step.
5. If configured and preflight is valid, proceed with a bounded real-model smoke and then the suite, recording failures honestly. No new ChatGPT authorization round is required for ordinary small batches.

ChatGPT can review PRs and results through GitHub when asked. Codex does not need to copy long documents into a chat or await minute implementation-level approvals.

## 10. Explicit limitations and decisions deferred

- Ten selected cases measure scenario-level behavior, not a statistically representative production success rate.
- Natural-language claim support cannot be fully proven by regex; manual evidence audit is mandatory.
- Some platforms may impose dynamic availability or IP/device security checks; they are out of this frozen offline evaluation.
- No evidence currently supports an asserted real-model success rate or token cost.
- Model/provider settings must be verified before real calls; Codex notifies the user about missing LLM_API_KEY/LLM_BASE_URL/provider/model settings without asking to reveal values. A reasonable bounded test batch needs no extra budget approval; no high-volume or scheduled paid batches without explicit user request.
- The user's local Codex may run workloads that I cannot access; only committed, redacted reproducible evidence is available for independent remote review.
