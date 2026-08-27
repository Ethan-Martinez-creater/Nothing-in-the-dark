---
name: social-normalization
version: 1.0.0
description: Normalize raw posts and comments
tools: [collect_social_posts]
permissions: [write_database]
inputs: [raw_posts, raw_comments]
outputs: [normalized_posts, normalized_comments]
cost_tokens: 500
cancellation: restartable
---
# Social Normalization

Keep raw payloads immutable. Normalize posts and comments separately, preserve reply
relationships, use platform plus native ID as the deduplication key, and record every
lossy conversion as metadata.
