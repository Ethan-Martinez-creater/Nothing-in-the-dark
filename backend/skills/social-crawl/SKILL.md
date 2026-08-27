---
name: social-crawl
version: 1.0.0
description: Bounded five-platform evidence collection
tools: [collect_social_posts]
permissions: [crawl_platform, write_database]
inputs: [keywords, platforms, time_range, sample_limit]
outputs: [posts, comments, crawl_report]
cost_tokens: 1000
cancellation: checkpointed
---
# Social Crawl

Clarify keywords, platforms, time range and sample limits before crawling. Crawling
requires approval. Never claim completeness: report platform failures, truncation and
time-filter gaps. Preserve native IDs and source URLs for every accepted record.
