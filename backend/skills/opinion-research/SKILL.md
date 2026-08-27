---
name: opinion-research
version: 1.0.0
description: Evidence-grounded opinion research
tools: [search_social_evidence, classify_sentiment, analyze_opinion]
permissions: [read_database]
inputs: [topic, platforms, time_range]
outputs: [sentiment, opinion_analysis, platform_stats]
cost_tokens: 8000
cancellation: restartable
---
# Opinion Research

Use retrieval to select representative evidence and deterministic tools for counts,
trends and clusters. Interpret clusters with the model, but attach source IDs to every
finding and distinguish measured distributions from model interpretation.
