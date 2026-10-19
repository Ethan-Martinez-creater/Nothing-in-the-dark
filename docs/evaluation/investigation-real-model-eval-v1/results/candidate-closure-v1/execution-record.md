# Candidate execution and validation record

Execution code: `0518eec1bf8fb49d3718d7916d6e6164866df31f`. All five model tasks report this same SHA. The candidate checkout was clean before inference. Subsequent evidence files do not change the execution code. The pre-run [PLAN](PLAN.md) is in this commit.

## Focused local tests

On Windows/Python3.11 with the existing backend environment, set backend `PYTHONPATH`, `PYTHONDONTWRITEBYTECODE=1`, and TEMP/TMP to a fresh E-drive artifact directory. Retain all test fixtures; no recursive cleanup was performed by the agent. Tests requiring pytest file fixtures needed normal host permissions because the restricted sandbox produced WinError5 on its newly created E-drive temporary directories. Those setup errors were not counted as code failures or passes.

```powershell
python -B -m pytest tests/test_execution_policy_runtime.py tests/test_agent_runtime.py tests/test_agent_loop.py tests/test_execution_policy_api.py tests/test_investigation_diagnostics.py tests/test_investigation_evaluator_audit.py tests/test_agent_eval_real_source_evaluators.py -q -o addopts= -p no:cacheprovider --basetemp=artifacts/pytest-integrated-final-01
```

**70 passed in20.82s.** Includes actual production GraphWorker delegation/finalization, writable-mode compatibility, forged child policy, shared parent/child three-call limit, a five-call failure batch, unknown/duplicate call IDs, ten concurrent reservations using two Repository instances, API validation, citation/fault counterexamples, source seeding and independent-layer diagnostics.

```powershell
python -B -m pytest tests/test_agent_eval_contract.py -q -o addopts= -p no:cacheprovider --basetemp=artifacts/pytest-compat-final-01
```

**22 passed in155.64s**, running the unchanged old24 scripted task scenarios after runtime integration. A prior evaluator-only compatibility check also passed22/22 before runtime integration; it is not additional real-model evidence.

Changed/new evaluator, seeding, API, diagnostic, archive-audit and runtime modules/tests passed targeted Ruff checks. Two touched legacy modules retain pre-existing lint findings (`agent_eval.py` imports/style and a long existing crawler comment in `tool_factory.py`); no unrelated formatting sweep was performed. `git diff --check` passed. The test authoring sequence included two test-code mistakes (missing required `approve_crawl`, wrong Repository listing method), corrected before the final integrated run; no model requests occurred during those attempts. Several helper-agent turns hit Codex model capacity; their interrupted work is not an eval outcome or test pass.

## Real candidate

The user-confirmed fresh settings fingerprint matched the previously successful receipt, still within24h. Reused this authenticated configuration without exposing/copying the Key. All three current production routes resolve to `deepseek-flash`.

Run from backend, using the existing private configuration and receipt paths:

```powershell
python -B -m app.scripts.run_investigation_study --tasks IR01,IR02,IR08,IR09,IR10 --attempt-label candidate-closure-v1 --config-file ../../real-eval-main/backend/.env --preflight-receipt ../../real-eval-main/backend/artifacts/agent_eval/real_v1/preflight-01.json --output-dir artifacts/agent_eval/candidate_closure_v1/candidate-01
```

Serial attempt started2026-10-19 07:37:20 UTC (15:37:20 Shanghai). All five tasks reached execution status completed. No extra real-model task/retry/probe was run. Eleven SDK create measurements succeeded; complete returned token totals74051 input/2948 output. Pricing is unverified; monetary cost is null. Exact per-case times, request records and tool/prompt/policy/fixture identities are in the public [manifest](runs/candidate-01/manifest.json) and adjacent JSON.

Private reports, requests, trace exports and per-task SQLite remain under `backend/artifacts/agent_eval/candidate_closure_v1/candidate-01`, ignored by Git. Public artifacts are curated synthetic excerpts and original hashes, not complete raw-log access. No production DB, platform login state or remote deployment was used.

## Integrity and limits

Existing frozen dataset/rubric and original public result Git contents are unchanged. Windows checkout CRLF conversion affects some copied documentation bytes; comparison accounts for checkout newlines and checks original Git contents separately. Retained original raw reports are verified against their previously published SHA256 values. The IR08 original database's pre/post inspection SHA matches the recorded forensics digest.

Public JSON parses; CSV has five complete provisional reviews and six rubric dimensions each. Case report/trace/request hashes and five execution identities match local retained artifacts. All five business snapshots match before/after. Public files are checked for the configured secret and absolute local paths; neither is published.

No semantic success-rate estimate, causal provider diagnosis, PostgreSQL integration claim or production-readiness approval follows. Candidate artifacts retain the three unanswered analysis tasks. A later independent GitHub review and user's merge decision remain pending.
