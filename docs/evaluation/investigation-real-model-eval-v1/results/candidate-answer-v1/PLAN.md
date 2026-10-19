# Bounded investigation answer-quality iteration

Authorized by the user after the accepted evidence milestone, tracked in [Issue #5](https://github.com/Ethan-Martinez-creater/Nothing-in-the-dark/issues/5). Base is main `3574d7273151997780bab71e9efb27252e2825fb` (PR #4 merged). This plan is committed before inference.

## Immutable comparators

Original baseline execution `518267b`, evidence `ecbce18`, automatic4/10 and Codex-provisional3/10 remain unchanged. Closure candidate execution `0518eec`, evidence `d59c63f`, selected automatic5/5 and provisional2/5 remain unchanged. Original frozen task/data/rubric suite SHA256 stays `c63ed42883d16e73ec0594b9c023fd65a34c90fa10a48f952d887bf2f21d590d`.

## Hypotheses and minimal changes

1. IR02/IR08/IR09 previously stopped after tool allowance, suppressing synthesis even after successful reads. Keep the exact same shared tool budget and read-only boundaries, but allow one tool-free synthesis operation per read-only run with successful tool observations, bounded to15s. No synthesis after all-failed reads, invalid requests or blocked writes. Empty/failed/tool-requesting output falls back honestly; cancellation propagates. An operation is one gateway call; SDK transport behavior remains separately observed. Per-run checkpoint/context guards prevent ordinary loop repetition; exactly-once provider delivery across a process crash is not claimed.
2. Expose remaining shared allowance and align read-only expert text return with coordinator instructions. Source wording must distinguish self-report, independently verified fact and candidate; missing materials are not proof of their absence.
3. Only `embedding_worker_unavailable` degrades to existing case/platform/time-scoped keyword retrieval. Return explicit degradation/mode metadata. Do not silently swallow other errors or use semantic sample retrieval for exact statistics.
4. Add supplementary `answer-delivery-v1`: runtime-only analysis stops are not delivered answers; verified persistent-fault refusals are reviewable; model text is only eligible for semantic review, never an automatic assertion of correct/complete investigation. Preserve E1–E13 semantics and all historical outcomes.

## Verification before paid execution

Focused runtime and production GraphWorker tests cover usage accounting, disabled-tool synthesis, all-failure refusal, timeouts/cancellation, forged tool calls, shared child budget and no writes. Retrieval tests cover degradation scope and error separation; delivery counterexamples distinguish expected refusal from unanswered analysis. Run the existing24 scripted compatibility scenarios, not a repository-wide repair sweep. Add targeted tests to CI; no CI job needs real model keys or live platform state.

## Bounded real candidate

Run once each **IR01, IR02, IR06, IR08, IR09, IR10**, serially, in a new ignored artifact directory and publish only under this separate directory. IR01/IR10 are controls, IR02/08/09 target lost synthesis, IR06 targets source overstatement. This is six frozen tasks, not a full ten-case resweep. Verify the private fresh-config receipt; if expired, refresh a small authenticated probe using the already authorized configuration. No repeated authorization question or secret disclosure.

Record actual outputs, call counts, injected faults, root/child policy/finalization events, business state deltas, retrieval degradation and provider/cancellation facts. Independently score each task against the unchanged rubric and distinguish runtime refusal, model synthesis and goal completion. No target success rate or optimization retry is promised. Any task failure is retained; further model attempts require a documented validity reason rather than dissatisfaction with a score.

The real candidate may or may not delegate before exhaustion. Scripted expert-path tests do not become a claim of same-path real-model reproduction. Provider variability, missing embeddings, keyword recall limits, untested PostgreSQL reservation locking, small sample, provisional adjudication and unknown monetary cost remain disclosed.
