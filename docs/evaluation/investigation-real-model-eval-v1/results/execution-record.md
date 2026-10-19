# Execution record

Manifest timestamps retain their explicit UTC offsets; summary wall times use **2026-10-19 Asia/Shanghai**. Work used the isolated evaluation worktree, without starting web services, using platform credentials, crawling public sites or touching the remote deployment.

## Preparation and controls

- Planning PR #2 was merged. Preparation/evidence is tracked in [PR #3](https://github.com/Ethan-Martinez-creater/Nothing-in-the-dark/pull/3), [Issue #1](https://github.com/Ethan-Martinez-creater/Nothing-in-the-dark/issues/1).
- Execution code SHA: `518267bb2202568a5a894ce5317715877a2374ed` (committed and pushed before these task runs). Result-document commits are later and do not imply a model rerun.
- Focused data + preflight checks: **12 passed**. Focused orchestrator checks: **2 passed**. Integrated harness and existing 24-task contract-suite tests: **28 passed in 427.63s**. Contract checks use a scripted gateway and are not real-model effectiveness results.
- Existing-suite integrated command, executed from `backend` with project-local TEMP/TMP and PYTHONPATH: `python -B -m pytest tests/test_investigation_real_harness.py tests/test_agent_eval_contract.py -q -o addopts= -p no:cacheprovider --basetemp=artifacts/pytest-integrated-02`.
- Earlier quota-interrupted offline contract/test attempts did not produce final reports. They are **INTERRUPTED / NO_FINAL_REPORT**, not PASS, FAIL or real-model results. Their existing local DB files were preserved. The later completed test run above is a separate attempt, not an overwrite.

## Fresh provider validation

The user explicitly confirmed replacing all six required LLM settings. Before task execution, a minimal authenticated connectivity request and a two-request tool-call/result round trip succeeded. Requested and returned model IDs were `deepseek-flash`. These three setup requests are **not dataset tasks** and are excluded from case scores.

Local artifacts: `backend/artifacts/agent_eval/real_v1/connectivity-probe.json` and private `preflight-01.json`. The latter binds the secret configuration and is intentionally not published. This report does not contain API keys or their fingerprints.

## Real attempts

1. `feasibility-02`: IR01 and IR05, one each. Both executed through the verified production gateway. Deterministic checks passed; provisional manual acceptance is reported separately.
2. `baseline-01`: IR01–IR10, one each, serially, with the same frozen code/config/fixture/rubric. Every failure remains in its original report and the public failure ledger.
3. `repeat-01` and `repeat-02`: IR09/IR10 once per directory. IR08 was not repeated because its synthetic-case no-mutation failure was decisive. All16 task attempts share code SHA `518267b`, one model configuration, one coordinator prompt hash and one tool-schema hash.

Exact commands and provenance are in each attempt's `runs/<attempt>/manifest.json`. See [RUNBOOK](../RUNBOOK.md) for PowerShell environment setup and safeguards. Each underlying CLI invocation uses a fresh report DB and an independently seeded task DB. The orchestration checkpoint calls the same production runner; it does not supply expected answers in real mode.

No raw report or failed attempt was deleted. Repeats use distinct directories and labels. A completed attempt means execution finished, not that tasks passed. Independent GitHub evidence audit and final approval remain pending.

## Publication checks

The final41-file public evidence package covers16 actual task attempts. Local validation checked JSON/CSV structure and complete provisional scores, raw-report digests, unchanged frozen-file hashes, a single execution commit/configuration/prompt/tool schema, absence of the configured API key in public text and absence of absolute local paths. All wrapper and per-case manifest timestamp offsets were verified as `+00:00`; summary times are explicitly converted to Asia/Shanghai. Source-map exceptions and abbreviated expert-input excerpts are labelled. No new model calls were used for publication checks.
