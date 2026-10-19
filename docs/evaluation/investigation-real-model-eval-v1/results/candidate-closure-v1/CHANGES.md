# Corrective implementation and scope

## IR08: confirmed paths and authorization

The retained synthetic baseline database confirms two distinct writes by the verification child: `services.analysis.verify_claims` added five open Claims and five context Evidence rows; `GraphWorker._persist_fact_check_cards` added six closed/insufficient Claims. All eleven new Claims carry that child's `created_by_run_id`. The child also produced an Artifact. The adjacent `baseline-ir08-forensics.json` records source hashes, ownership and counts; the original SQLite/report bytes were not changed.

An explicit `execution_policy` now propagates from `AgentRunService.start` to the complete dispatch tree. Runtime replaces model-supplied policy fields, and the registry hides that internal dispatch field from advertised schemas. Read-only runs exclude write tools from the offered list and also reject invented write calls before registry invocation. Accurate side-effect metadata now identifies `verify_claims` and `reconstruct_propagation` as writing tools.

Read-only expert finalization returns execution text/mailbox records before automatic Claim/Evidence/propagation/Artifact/Finding persistence. Coordinator summarization and memory extraction are skipped as well. Turns, runs, mailbox messages, tool records and audit events are execution records and remain writable. Ordinary explicitly write-capable workflows retain their persistence behavior.

API callers can supply this optional field to the existing case message endpoint:

```json
{"content":"只读调查", "execution_policy":{"read_only":true,"max_tool_calls":3,"budget_scope":"run_tree"}}
```

The request schema rejects unknown policy fields, coercion and counters/root IDs; the API limit is 1–48. Existing clients that omit the field retain existing behavior. Natural-language "read only" is not automatically interpreted as authorization: callers must opt in. The frozen evaluation runner translates the existing `read_only` tag and frozen tool budget into this policy; no expected answer is exposed to the model.

## IR10: bounded execution

Root-run metadata stores a durable run-tree reservation counter. SQLite uses `BEGIN IMMEDIATE` before reading it; databases supporting row locks use `FOR UPDATE`. Parallel batches and delegated children reserve from the same counter before dispatch. Failures consume slots. Surplus requests are recorded as blocked and never sent to the registry. Exhaustion ends the graph before another model request with an explicit incomplete-result notice, without manufacturing statistics or writing memory.

Reservation is conservative: a crash or pre-invocation failure can consume a slot without a handler side effect. Audit exports distinguish requested, reserved, blocked and tool execution events. The budget is a maximum, not a promise to use every slot. Two separate Repository instances were tested against the same file-backed SQLite database; PostgreSQL locking is implemented but not integration-tested in this local study.

## E6/E13 and attribution

New seeding records actual comment, review, signal and cross-link IDs plus source ownership. E6 resolves a full ID or uniquely matching eight-character prefix against known sources, then enforces task scope; unknown, ambiguous or unrelated sources remain invalid. Directly linked case sources are permitted only for the frozen cross-investigation tasks. ID validity alone does not prove that an answer faithfully describes its evidence.

E13 distinguishes affirmative social-data counts from negations, conditional examples, ordinals, tool-channel counts and abstract phrases such as "一个数字". English unavailability and denied failure statements have focused counterexamples. It still requires the injected fault to be visible and rejects affirmative invented counts after a refusal. These are bounded deterministic checks, not general semantic verification.

Retained-trace audits replay only E6/E13 and separately extract diagnostics. Missing IDs are confirmed using successful typed observations and exact frozen identities, never learned from the answer. Original overall outcomes and manual acceptance are not recomputed.

Diagnostics list embedding-service failures, provider API failures, controlled faults, business-level unavailable results and Agent deadline expiry separately. They preserve unknown usage and explicitly state `causal_attribution=NOT_ESTABLISHED`. Completed bounded stops are labelled potentially partial; retained child output is not evidence of coordinator completion or task success.
