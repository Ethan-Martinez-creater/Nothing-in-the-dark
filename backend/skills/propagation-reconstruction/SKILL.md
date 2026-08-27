---
name: propagation-reconstruction
version: 1.0.0
description: Observed and inferred propagation
tools: [reconstruct_propagation, query_propagation]
permissions: [read_database]
inputs: [topic, platforms, time_range]
outputs: [propagation_edges, propagation_graph]
cost_tokens: 6000
cancellation: checkpointed
---
# Propagation Reconstruction

Observed edges require native references or explicit links. Temporal order alone may
only create an inferred candidate. Store feature scores, evidence IDs, algorithm
version and uncertainty for every accepted edge.
