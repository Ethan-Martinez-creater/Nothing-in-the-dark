---
name: evidence-review
version: 1.0.0
description: Critically review evidence entailment
tools: [query_evidence, query_claims]
permissions: [read_database]
inputs: [claims, evidence_set]
outputs: [evidence_review, entailment_verdicts]
cost_tokens: 5000
cancellation: abortable
---
# Evidence Review

Independently verify that each cited source exists and entails the attached conclusion.
Reject circular citations, missing IDs and claims that are stronger than the evidence.
