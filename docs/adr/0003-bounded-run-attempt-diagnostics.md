# Bounded attempt diagnostics for the run explorer

> **Superseded security boundary (current policy):** The original loopback-only
> Runs API decision below is retained as historical ADR context. Runs endpoints
> now follow the shared web authorization policy: `token`, `disabled`, or
> `capabilities` mode, with the configured `runs` capability and the same exact
> Host/Origin checks as other API routes. Deployments exposing Runs remotely
> must use the applicable bearer/capability policy and must not rely on
> loopback-only enforcement.

## Decision

Every acquired worker lease remains a single durable Run attempt identified by the existing SQLite `runs.run_id`. Continuation turns, workflow phase changes, and in-process backend retries are lifecycle events within that attempt; a later redispatch after terminal exit acquires a new `run_id`. The nullable numeric `attempt` field remains retry metadata and is not an identity. The run explorer adds an additive schema migration with summary fields on `runs` and a new attempt-event table; it does not reactivate the inert `run_events` table left by the removed flow engine.

Symphony records only normalized, operator-relevant lifecycle facts: acquisition, session and turn boundaries, selected failure/retry/compaction excerpts, workspace commit snapshots, and completion. Arbitrary backend payloads, assistant messages, tool transcripts, prompts, environment variables, session identifiers, and complete stdout/stderr are not stored. Selected failure excerpts pass through a conservative credential-shape redactor before persistence and are not a safe place to intentionally submit secrets. Each persisted string is truncated, each event payload has a fixed size ceiling, each attempt retains a fixed maximum event count with deterministic oldest-first eviction, and events/failure excerpts are retained only for a bounded number of recent terminal attempts.

The `runs` list remains backward compatible. A run-detail endpoint returns one summary and its retained timeline; a diagnostic endpoint returns the same bounded evidence as an attachment. Because summaries contain local workspace paths and selected failure excerpts, all Runs endpoints accept loopback clients only, even when the broader board is explicitly network-bound. Search and filters narrow summaries without changing dispatch behavior. Polling refreshes the browser view; this feature adds no worker WebSocket stream.

## Authority and lifecycle boundaries

The run explorer is observational. Markdown tickets remain workflow state, SQLite run leases remain dispatch authority, and release-gate records remain application-release authority. Attempt events cannot approve a release, move a ticket, resume a worker, or prove a complete transcript. Missing diagnostics degrade the explorer but must not fail a worker or weaken lease fencing.

A commit reference is resolved off the asyncio event loop only after the workspace `after_run` hook completes, because that hook may create or amend the turn commit. Absence is represented as unknown, never inferred from a later branch tip.

## Upgrade boundary

The migration is additive and transactional. Existing callers of `complete_run` and `recent_runs` remain valid through optional fields and filters. Existing attempts appear with empty timelines and unknown summary fields. The inert flow-engine tables remain untouched for downgrade and historical compatibility.

## Consequences

Operators gain one place to explain attempts and download a reviewable diagnostic bundle without mining the service log. The evidence is intentionally incomplete: deep tool traces, artifact galleries, raw-log retention, replay, and live event streaming require separate decisions because they have different privacy, storage, and lifecycle costs.
