# Continue crashed work from completed-turn checkpoints

## Decision

Symphony may continue an interrupted worker only from its latest host-recorded completed-turn boundary. An interrupted turn is never considered complete and may run again. Durable continuation therefore provides at-least-once recovery, not exactly-once execution of arbitrary agent tools or external side effects.

The interrupted Run attempt remains terminal evidence and its successor receives a new `run_id`. Symphony never reattaches orchestration authority to the predecessor and never adopts a surviving operating-system process. Before a checkpoint becomes eligible, startup recovery must fence the predecessor, verify the recorded backend process incarnation, terminate that process group, and durably finalize it as orphaned. A missing/mismatched process incarnation is never signalled; an ambiguous cleanup keeps the fence and prevents continuation. Expired leases with recorded backends follow the same reap path rather than becoming leaseless writers.

Exactly one successor may claim a checkpoint. Selecting the predecessor and creating the linked successor are one fail-closed SQLite transaction. Ordinary dispatch may retain its historical fail-open behavior when diagnostics are unavailable, but session continuation cannot: an unreadable or contended continuation store produces no resumed session.

The checkpoint contains only the minimum private recovery data: the exact backend session identifier, completed-turn count, workflow state, backend kind, and timestamp. The session identifier is bounded, remains in the local authority database, and is never returned by the Runs API, diagnostic bundle, logs, or UI. The public execution history may expose only the predecessor/successor run link and non-secret boundary metadata.

A backend may resume only when it supports an exact, explicit session identifier. Codex, Claude Code, Pi, Prime Agent, and OpenCode participate by binding the next invocation to that exact identifier; ambient “continue the last conversation” behavior is forbidden. Codex can verify its RPC attach before a turn. Per-turn CLIs can verify only when the explicitly bound invocation completes, because a separate probe would itself create or mutate a turn. Gemini, AGY, Kiro, unknown adapters, agent-kind changes, workflow-state changes, and invalid or missing checkpoints start a fresh session instead.

Application-release verifier and finalizer runs do not reuse sessions across Run attempts. Release authority is bound to an exact `run_id`; after interruption a new run must re-establish authority and execute with a fresh agent session.

## Checkpoint boundary

The host writes a checkpoint only after the backend reports successful turn completion and the workspace `after_run` hook finishes. This ordering makes the workspace and any hook-created commit part of the completed boundary. Session-start, turn-start, partial output, and diagnostic events are observational and cannot authorize recovery.

A successor carries the predecessor's completed-turn count so total-turn budgets survive restart. A preflight-capable backend rejection falls back to a fresh first-turn prompt before any work begins. For a per-turn CLI, the first invocation uses the continuation prompt with its explicit exact-ID flag. If that invocation rejects the ID, the recovery attempt fails closed, does not advance its inherited checkpoint, and a later dispatch starts fresh rather than replaying the consumed predecessor checkpoint.

## Upgrade and compatibility

The SQLite migration is additive. Existing runs have no checkpoint and follow the current fresh-dispatch behavior. Existing run-list fields and diagnostic payloads remain valid; consumers must ignore additive continuation-link fields. Disabling crash continuation preserves checkpoint evidence but does not claim it.

## Consequences

Operators lose less context after service or host failure while the existing lease, workspace, release, and run-history authorities remain intact. Recovery may repeat the interrupted turn and any non-transactional action it performed before the crash. Preventing all such duplication would require transactional tool protocols and external idempotency keys, which are outside this feature.
