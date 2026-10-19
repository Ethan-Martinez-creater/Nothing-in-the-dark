# Retained-trace E6/E13 audit — no model rerun

Audited with candidate `0518eec`, `investigation-evaluators-v2`. The JSON records include original report SHA256, original/repaired individual outcomes, confirmed added IDs and independent layer diagnostics. They explicitly set `baseline_or_manual_acceptance_recalculated=false`. They are additional adjudication evidence, not replacement baseline reports.

| Retained task | Original check → repaired check | Interpretation |
|---|---|---|
| Baseline IR03 E6 | FAIL → PASS | Actual review/comment UUIDs confirmed against successful typed observations and frozen identities; no invented model citation established by the original E6 failure. |
| Baseline IR04 E6 | FAIL → PASS | Real signal ID confirmed from frozen detector/title/related-case identity. Missing required post anchor remains an independent semantic failure. |
| Baseline IR06 E6 | PASS → PASS | Old detector missed short IDs; repaired detector resolves real abbreviated comment/post references. Incorrect evidence wording remains independent. |
| Repeat-02 IR09 E6 | FAIL → PASS | Real comment references resolve; cancelled execution and incomplete answer remain failures. |
| Baseline / repeat-01 / repeat-02 IR10 E13 | FAIL → PASS, all three | Negated zero, conditional zero, abstract/tool-channel count phrases and English refusal are handled correctly. Original nine/four/ten-call stop failures remain unchanged. |

Other audited E6/E13 outcomes remain unchanged. Empty-answer E6 PASS does not imply citation adequacy. New counterexamples still reject fabricated UUIDs, ambiguous prefixes, unrelated-case IDs, affirmative invented counts after a refusal, and absence of an actual fault injection.

Reproduce from the backend directory, supplying retained private raw artifacts and a fresh non-existing output filename:

```powershell
python -B -m app.scripts.audit_investigation_evaluators --attempt-dir ../../real-eval-main/backend/artifacts/agent_eval/real_v1/baseline-01 --tasks IR02,IR03,IR04,IR06,IR09,IR10 --output artifacts/agent_eval/candidate_closure_v1/audit-new-baseline.json
```

Repeat task selection IR09,IR10 against each repeat directory. The script does not create a model gateway or write the retained databases/reports. It refuses an existing audit output. Sources: [baseline](offline-audit-baseline.json), [repeat-01](offline-audit-repeat-01.json), [repeat-02](offline-audit-repeat-02.json).
