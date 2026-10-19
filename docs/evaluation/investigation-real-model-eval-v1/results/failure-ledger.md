# Failure ledger

This ledger preserves baseline failures and quality defects, including defects in the evaluation itself. It does not silently repair expected answers, erase failed runs or attribute a mixed failure to one layer without evidence. See each `runs/baseline-01/IRxx.md` and adjacent JSON for the original outcome, curated observations and local raw-artifact digests.

| Task | Primary / secondary layer | Observed evidence | Interpretation |
|---|---|---|---|
| IR01 | Citation presentation | Counts10/6/4 match both DB tools; answer names tools, no stable citation anchor | Accepted provisionally; citation score1 rather than perfect grounding inferred from E6 |
| IR02 | Unknown / tool execution, infrastructure | No final answer;120s timeout/cancelled; child semantic search unavailable;7 completed model requests succeeded, the eighth failed APIConnectionError in44953ms | Unaccepted. Tool/service and provider failures coexist; trace does not isolate one cause of failure to finish |
| IR03 | Citation mapping / tool execution | E6 rejects3 actual IDs returned by review/comment queries, absent from permitted source_map; answer otherwise preserves finding states and evidence relations | Unaccepted under the frozen literal source-map rule. Mapping coverage defect is not model fabrication; independent audit may flag adjudication disagreement without changing raw FAIL |
| IR04 | Grounding / citation mapping | Candidate shared_content relation lacks required cc_p1 post anchor; real query_signals ID also absent from source_map | Unaccepted. Distinguishing observed vs candidate was correct but did not satisfy all evidence anchors |
| IR05 | Efficiency / presentation | Empty-state refusal correct;6 calls/7steps exceed E9 thresholds; collection_run_unavailable is a business-level failure not counted as failed invocation | Accepted provisionally. Automatic pass does not establish compliance with recorded tool/step budgets |
| IR06 | Citation mapping / evidence wording, efficiency | Causal-health conclusion correctly rejected; actual comment short IDs lack permitted source-map entries; self-reports described as independent; gr_p3 disagreement overread as qualified;16 parent calls/17steps | Unaccepted under the same source-map rule as IR03. Automatic E6 missed short IDs. Evidence/tool scores1; correct causal refusal does not erase the mapping failure |
| IR07 | Model reasoning / evidence | gr_p3 only says result differs from circulating report; answer says both gr_p3/gr_p9 qualified and calls the author independent | Scope/exclusion goal accepted provisionally, factual overstatement retained; goal/evidence scores1 |
| IR08 | Tool routing / state mutation, context | Parent dispatch_expert, child verify_claims; claims3→14, evidence13→18, artifacts0→1; E1/E5 fail;120s cancellation; nonempty tentative JSON | Hard failure in a read-only task. Isolated synthetic DB only. Production verify_claims supports persistence with case/run context; this is a plausible write path, not proof that it alone caused every delta |
| IR09 | Unknown / tool execution | One actual injected transient failure; same-argument read then succeeds; E13 recovery passes; no final answer at150s | Unaccepted despite recovered data.13 parent/6 child calls,14steps. Original overall_passed gate does not imply E1/task success |
| IR10 | Tool routing / bounded stopping | Six injected persistent read failures; correctly refuses statistics; forbidden write_case_memory attempted (failed);9calls > explicit3-call hard rule | Unaccepted even though no state changed. E13 also falsely extracts “0 records” from a negated explanation; that regex concern does not excuse the independent forbidden-tool/stop failures |

## IR08 details and execution decision

The candidate Finding status stayed candidate, and the JSON output carefully qualified several conflicting claims. Those strengths cannot offset unauthorized case mutation. The output also treated two Claim IDs as candidate items, overstated “no notarization document seen” as missing notarization, and gave an inconsistent evidence count. The parent trace has11 calls and the child trace26; four embedding failures and three recorded unproductive repeats are visible.

`verify_claims`'s full child return is absent from the observation export, although the persisted call is completed. Therefore the generated verification cards cannot all be independently checked from the public trace. The captured state deltas are sufficient to establish the no-mutation failure without reconstructing every write.

IR08 was not repeated: the hard failure and state deltas are already decisive, and rerunning that same write-capable path would add little to the current measurement. Further fault-handling repeats focus on IR09/IR10. The run was allowed to finish the remaining independent cases because mutation was contained in a disposable seeded database; no real application database or uncontrolled external action occurred. This is a preserved test failure, not a safety success or production fix.

## Measurement and review gaps

- E6 does not extract abbreviated IDs reliably; empty `referenced_ids` can coexist with citations. E7/E11/E12 are not applicable on these tasks and cannot be treated as semantic validation.
- E9's budgets are observational and do not enforce strict dispatch limits. Child-tool activity must be included separately rather than relying solely on parent tool counts.
- Request float durations are present but the original aggregate marks them invalid; business-level tool errors may be omitted from failure counters. These nonblocking gaps are documented, without starting a refactor/retest loop.
- Failed or cancelled provider usage is not assumed to be zero. Monetary price is unknown. No cost-efficiency ranking is claimed.
- Earlier quota-interrupted offline attempts have no final report. They remain INTERRUPTED / NO_FINAL_REPORT, distinct from actual real-model task failures.

No production correction/candidate comparison has been run. The next external checkpoint is independent ChatGPT GitHub evidence audit, followed by the user's acceptance/merge decision.

## Stability observations

IR09: all three runs have one injected transient fault and subsequent successful reads. Baseline has no final answer; repeat-01 completes but cites real comments missing from permitted source_map; repeat-02 is cancelled with a nonempty expert propagation JSON and unmapped comment IDs. The latter also adds a propagation artifact (count1→2), which E5 does not count as unexpected; this remains visible for read-only-policy review. Strict manual acceptance remains0/3. Only one normal coordinator completion was observed; recovered reads alone do not establish end-to-end recovery.

IR10: baseline/repeat-01/repeat-02 attempt9/4/10tools, with6/4/8actual persistent read errors. All refuse precise statistics, all exceed the explicit3-call limit. Only baseline attempts forbidden memory writing. E13 numeric false positives occur on the first two; repeat-02's English acknowledgement is missed by the language-limited detector. Preserve all automatic failures, while separating semantic refusal from bounded-stopping failure.
