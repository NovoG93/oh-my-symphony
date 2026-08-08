# Acceptance Run B — maintenance request → merged feature on existing codebase

**Date:** 2026-02 · **Branch:** `minimal` @ 73f7323 · **Backend:** claude (real CLI) · **Board:** 4-lane default with renamed **Document** lane

## Setup
Host repo = the app delivered by Acceptance A (merged main). One maintenance ticket filed per
the chat intake format ("Add Clear-completed button", Goal/Scope/Acceptance/Evidence,
`--request REQ-2`). Board: Todo → In Progress → Verify → Document. HEAD's succinct prompts.

## Result
Todo → In Progress → Verify → Document → **Done** in 19 dispatched turns.
Merged: `d0ae3f5` (merge with merge-tree preflight logged against current main).
Merged tree verified: `node run-tests-node.js` → **20 passed, 0 failed** (16 before; 4 new
tests for `clearCompleted`); feature present in app.js + tests.js; footer button + persistence
per acceptance criteria. Ticket carries full contract chain: Plan → … → Review, Security Audit,
QA Evidence, AC Scorecard, Merge Status, Learnings, Wiki Updates, As-Is→To-Be.

## Operator interventions & findings (adversarial-review input)
1. **Workspace→board reachability is not mechanically verified.** Scratch setup initially
   lacked the kanban symlink hook; the worker silently wrote all ticket updates (incl.
   `state: Verify`) into a self-created LOCAL `kanban/` copy — the board never saw them and
   the orchestrator re-dispatched forever. Fix candidate: doctor check + orchestrator
   detection ("ticket file modified in workspace but board unchanged").
2. **Default stall budget too tight for heavy Verify stages.** With default
   `stall_timeout_ms` (~300s) the Verify worker repeatedly died mid-stage after long local
   test/scan runs, before it could write the ticket → loop. Raising claude
   `stall_timeout_ms` to 900s let Verify complete in one turn. Fix candidates: per-stage
   stall budgets, or surfacing the tunable prominently, or resetting the stall clock on
   workspace file activity.
3. **claude CLI stream flakiness** (rc=1 / "stream unreadable: malformed lines") burned
   ~6 turns across A+B; every failure auto-paused correctly and resume worked. Fix candidate:
   auto-retry policy for known-transient stream errors (bounded), instead of pausing.
4. Wiki write-back location ambiguity: Document lane wrote the ticket's Wiki Updates section,
   but `docs/llm-wiki/` presence in the merged host tree differs between runs A and B —
   worth one canonical rule.

## Verdict
GREEN — maintenance path works end-to-end on an existing codebase, including the renamed
Document lane; all four findings are pipeline-hardening items, not functional failures.
