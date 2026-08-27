---
name: report-generation
version: 1.0.0
description: Generate citation-grounded reports
tools: [get_artifact, build_report]
permissions: [read_artifact, write_artifact]
inputs: [claims, evidence, report_type]
outputs: [report]
cost_tokens: 12000
cancellation: abortable
---
# Report Generation

Generate reports only from persisted artifacts. Keep measured facts, algorithmic
results and model inference visibly separate. Every important conclusion needs valid
evidence IDs; unresolved gaps must remain explicit.
