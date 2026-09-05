---
name: Use LangGraph for audit pipeline orchestration
status: Accepted
date: 2026-08-15
---

## Context
The audit pipeline has multiple stages (evidence extraction, versioning, calculations, findings) that need to be executed in a deterministic order while allowing easy parallelism for future extensions.

## Decision
We adopt **LangGraph** (from the `langgraph` library) as the orchestration engine.  The pipeline is expressed as a `StateGraph` with explicit nodes and edges, compiled once at import time and reused for every audit run.

## Consequences
* ✅ Clear, declarative workflow – easier to reason about and test.
* ✅ Native support for parallel fan‑out (used for the counterfactual debate).
* ⚠️ Requires the `langgraph` dependency (added to `requirements.txt`).
* ⚠️ All state must be JSON‑serialisable – we use `TypedDict` for the `FairPayState`.

## Related ADRs
* ADR‑002 – Counterfactual debate implementation.
* ADR‑003 – Batch audit concurrency model.
