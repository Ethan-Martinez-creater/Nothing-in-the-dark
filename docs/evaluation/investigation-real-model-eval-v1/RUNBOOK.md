# Real-model investigation v1 execution runbook

Coordination: [Issue #1](https://github.com/Ethan-Martinez-creater/Nothing-in-the-dark/issues/1).
Read PLAN.md, task-catalog.md and RUBRIC.md before execution. Criteria must be frozen
before inspecting model task answers. Contract results are not real-model quality results.

## Local isolation

Use the evaluation branch/worktree, never the user's live PostgreSQL database. The
`investigation_real_eval_v1` CLI only accepts a **new** SQLite report database beneath
`backend/artifacts/agent_eval/real_v1`; tasks each use another isolated temporary
SQLite database through the existing runner. ApplicationContainer, web services,
monitor/collection workers and browser credentials are not started.

Keep generated files on the project's drive. The examples are PowerShell 7 commands,
run from the selected evaluation worktree's `backend` directory:

```powershell
$evalPython = 'E:/miniconda3/envs/bettafish/python.exe'
$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$evalTemp = Join-Path (Get-Location).Path 'artifacts/agent_eval/real_v1/temp'
New-Item -ItemType Directory -Path $evalTemp -Force | Out-Null
$env:TEMP = $evalTemp
$env:TMP = $evalTemp
```

Retain task databases and failed attempts for inspection. No automatic recursive
deletion is performed by the task database wrapper. Use a new `--output` and pytest
`--basetemp` for each attempt; never rerun against an existing attempt path.

Disposable synthetic SQLite connections use memory journals and synchronous=OFF
to avoid Windows per-DDL fsync stalls. File-backed multi-connection isolation is
retained; this study does not measure crash/power-loss durability. The live
application database is never given these settings.

## Fresh model provisioning and preflight

All pre-existing LLM settings were declared expired. The user must replace the
complete configuration in the **evaluation worktree's** ignored `backend/.env`,
not merely fill a missing key. Do not copy the old main-worktree `.env`, since it
contains unrelated live database, crawler and account settings.

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=replace-securely-on-the-local-machine
LLM_FAST_MODEL=actual-tool-capable-model-id
LLM_REASONING_MODEL=actual-tool-capable-model-id
LLM_REPORT_MODEL=actual-tool-capable-model-id
LLM_MAX_CONCURRENCY=1
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=1
```

The actual production transport is OpenAI-compatible **Chat Completions**.
`LLM_PROVIDER` is a label, not a driver switch. Base URL is the API root, not the
complete `/chat/completions` URL. HTTPS is required, without URL-embedded secrets.
The production `model_for()` routes **fast, reasoning and report all to
LLM_FAST_MODEL**, ignoring the other two model IDs. This study retains that policy;
the manifest records both declared and effective routing. Model response IDs must
match the selected actual model ID; an unexpected response stops further calls.

After the user explicitly confirms fresh provisioning, run the bounded preflight:

```powershell
& $evalPython -B -m app.evaluation.investigation_preflight --config-file .env --probe --confirm-fresh-config --receipt artifacts/agent_eval/real_v1/preflight-01.json
```

This sends two short, non-sensitive production-gateway requests: a tool call and a
tool-result continuation. It records actual response model IDs and raw SDK usage.
It does not run a dataset task. The receipt binds the full model configuration
including key fingerprint and expires after 24 hours. Changing configuration needs
a new receipt. Keep it private/ignored; never publish the key-bound fingerprint.
Offline `--config-file .env` without `--probe` only checks presence and transport
shape and does not authorize a real run.

## Frozen evaluation sequence

Commit the implementation and frozen data before task runs. First execute focused
offline data/harness/preflight tests and the unchanged 24-task contract control.
Contract mode may synthesize answers from expected facts; real mode never does.

```powershell
& $evalPython -B -m app.scripts.run_agent_eval --suite-version investigation_real_eval_v1 --mode contract --candidate-label preparation --output artifacts/agent_eval/real_v1/contract-01.json
& $evalPython -B -m app.scripts.run_agent_eval --suite-version investigation_real_eval_v1 --mode real_model --tasks IR01,IR05 --candidate-label feasibility --llm-config-file .env --preflight-receipt artifacts/agent_eval/real_v1/preflight-01.json --output artifacts/agent_eval/real_v1/feasibility-01.json
& $evalPython -B -m app.scripts.run_agent_eval --suite-version investigation_real_eval_v1 --mode real_model --candidate-label baseline --llm-config-file .env --preflight-receipt artifacts/agent_eval/real_v1/preflight-01.json --output artifacts/agent_eval/real_v1/baseline-01.json
```

Run serially with the frozen task budgets, one gateway request in flight. A genuine
auth/permission error, unexpected model or repeated provider failure stops further
paid calls. Preserve infrastructure failures and mark unexecuted cases explicitly;
never replace them with contract results. Ordinary task failures remain observations,
not grounds to rewrite criteria. After a valid baseline, repeat IR08/IR09/IR10 only
when useful, using distinct attempt labels/paths, the same fixture and configuration.

For interruption-resilient execution, the following small orchestrator calls the
same CLI separately for each case and checkpoints an `attempt.json` after every
case. No second runtime or different expected answers are introduced:

```powershell
& $evalPython -B -m app.scripts.run_investigation_study --tasks IR01,IR05 --attempt-label feasibility --config-file .env --preflight-receipt artifacts/agent_eval/real_v1/preflight-01.json --output-dir artifacts/agent_eval/real_v1/feasibility-02
& $evalPython -B -m app.scripts.run_investigation_study --attempt-label baseline --config-file .env --preflight-receipt artifacts/agent_eval/real_v1/preflight-01.json --output-dir artifacts/agent_eval/real_v1/baseline-01
```

The orchestrator allows at most ten distinct tasks per invocation. A completed
attempt means execution finished, not that cases passed. Per-case deterministic
checks, null human acceptance and failure evidence are kept separately. No-report
infrastructure errors and provider hard stops block remaining requests explicitly.

## Evidence, measurement and review

The CLI produces raw report JSON/Markdown, per-task trace JSON files and a manifest
with file digests, fixture/rubric/task hashes, code SHA, model route, tool/prompt
hashes, command and environment. All raw outputs remain under ignored artifacts.
Source keys resolve to seeded IDs for manual claim-to-evidence checking.

Raw SDK usage is observed before production's default-zero conversion. If usage is
missing, tokens are null/NOT_MEASURABLE. Do not label the legacy CNY estimate as USD.
The historical production price table is not verified for the fresh provider, so
this study records estimated monetary cost as null/NOT_MEASURABLE until a verified,
versioned price is supplied. Request count, latency and returned tokens remain
measurable. Missing optional price metadata does not block the study.

Publish only curated synthetic evidence: final answers, stable source mappings,
ordered tool/error excerpts and manual 0–2 rubric scores with supporting spans.
Record deterministic acceptance and human scores separately. Human adjudication
starts PENDING and cannot be inferred from E1 or a contract pass. Independent GitHub
review is still required before final acceptance. Preserve every failed attempt and
distinguish local-only artifact hashes from actually published links.

Do not restart frontend/backend services, scrape live platforms, test Douyin, change
Finding/Review/collection semantics, fix unrelated regression failures, or introduce
a new Agent/runtime as part of this evaluation.
