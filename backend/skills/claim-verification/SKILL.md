---
name: claim-verification
version: 1.0.0
description: Verify claims against bounded evidence
tools: [search_social_evidence, verify_claims, query_claims, query_evidence]
permissions: [read_database, read_artifact]
inputs: [claims, evidence_query]
outputs: [claims, evidence, verdicts]
cost_tokens: 10000
cancellation: restartable
---
# Claim Verification

Split compound claims, retrieve supporting and contradicting evidence, then check
identity, time and context. Allowed verdicts include insufficient evidence. Never use
an uncited model recollection as evidence.
