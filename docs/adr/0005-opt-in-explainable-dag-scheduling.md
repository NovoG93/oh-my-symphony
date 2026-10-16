# ADR 0005: Keep DAG-aware ordering opt-in and explain it from dispatch authority

- **Status:** Accepted
- **Date:** 2026-08-11

## Context

Symphony stores ticket priorities and dependency edges, but its established dispatch contract is stable registration-order FIFO with a starvation-recovery bump. Making priority and critical-path ordering the default would silently reorder existing unattended workflows. A separate UI-only ranking would be safer but could disagree with the real dispatcher and mislead operators.

## Decision

Add `agent.scheduling_policy` with `fifo` as the compatibility default and `dag` as an explicit opt-in. After starvation recovery, DAG policy orders candidates by declared priority, remaining downstream critical-path length, then the existing registration key. Eligibility and dependency safety remain authoritative and unchanged.

The scheduler projection reuses the same eligibility decisions and ordering function as dispatch. Queue forecasts show deterministic positions and dependency waves, never wall-clock ETAs. Request grouping uses only the explicit `request` field; ungrouped tickets are standalone. A request projection includes the transitive prerequisite closure, including external-request blockers. Its execution summary is computed from exactly that returned graph, while the separately labeled global DAG rank chain explains cross-request downstream ranking.

A completed scheduler pass stores an immutable issue fingerprint with each decision. The API marks the projection stale when the current file board has drifted or when a later pass does not complete. Malformed cycles are collapsed for deterministic dispatch but reported as invalid execution order. Read endpoints enforce bounded graph work and publish only allowlisted decision reasons.

## Consequences

Existing workflows retain their order until operators opt in. DAG users get explainable priority and critical-path behavior without bypassing safety gates. Forecasts remain truthful but deliberately avoid unreliable duration promises. File boards receive complete prerequisite projections from their dependency snapshot; trackers without a complete mutable dependency snapshot report the feature as unavailable.
