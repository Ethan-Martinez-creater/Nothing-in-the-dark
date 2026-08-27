---
name: case-follow-up
version: 1.0.0
description: Answer with case memory and artifacts
tools: [search_social_evidence, write_case_memory, get_artifact]
permissions: [read_database, read_artifact, write_memory]
inputs: [question, case_memory, artifacts]
outputs: [answer, memory_updates]
cost_tokens: 4000
cancellation: restartable
---
# Case Follow-up

Resolve references against the current case, retrieve only relevant memories and
artifacts, and reuse existing results unless the user asks for fresh collection.
Treat user corrections as higher priority than inferred memory.
