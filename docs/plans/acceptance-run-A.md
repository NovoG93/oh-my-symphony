# Acceptance Run A — greenfield chat-shaped request → delivered app

**Date:** 2026-02 · **Branch:** `minimal` @ 1ea0739 · **Backend:** claude (real CLI) · **Board:** 4-lane default, succinct prompts

## Setup
Scratch dir with: empty host git repo (`project/`), 4-lane WORKFLOW.md (`Todo → In Progress →
Verify → Learn`) using the branch's succinct stage prompts, claude backend, max_turns=12,
workspace hooks: clone host repo / wip-commit per turn. One ticket filed exactly as the chat
intake protocol specifies for a SIMPLE request (validated `symphony board new --request REQ-1
--description-file -`, Goal/Scope/Acceptance/Evidence body): "Build a static to-do web app".

## Mechanical smoke (mock backend, separate scratch)
- Stage-ticket DAG (research←plan←review←build) created via validated CLI; cycle attempt rejected.
- `symphony board graph` rendered topological DAG.
- Orchestrator dispatched ONLY unblocked tickets; completing the blocker released exactly the next ticket.

## Pipeline traversal (real claude workers)
Todo →(auto-triage)→ In Progress → Verify → Learn → **Done**. 9 dispatched turns.
Ticket accumulated all contract sections: Plan, Acceptance Tests, Done Signals, Implementation,
Self-Critique, Pipeline Route, Security Audit, Review, QA Evidence, AC Scorecard, Merge Status,
Learnings, Wiki Updates, As-Is→To-Be Report.

## Delivered software (verified)
- Host repo `main` after run: merge commit d9b8b69 "Merge branch 'symphony/REQ1-TODOAPP'";
  merge-tree preflight logged (no conflict) before merge.
- App: index.html + app.js + style.css + tests.html/tests.js + run-tests-node.js.
- `node run-tests-node.js` **in the merged main tree**: 16 passed, 0 failed, 9 skipped
  (DOM cases; worker wrote a headless-Chrome proof harness for those under docs/…/work/).
- Evidence vault docs/REQ1-TODOAPP/{qa,work}: ac-evidence, acceptance re-run logs,
  merge-tree.log, integration-gate log, security rescan.
- Wiki write-back: docs/llm-wiki/{INDEX + 4 pages} (turn-budget, decision-log,
  headless-chrome-proof-harness, static-file-url-apps) — the learning loop works.

## Operator interventions (honest account)
- 3 × resume after auto-pause: claude CLI stream errors ("no result event rc=1",
  "stream unreadable: malformed lines") — environmental (nested claude session);
  Symphony's auto-pause → operator resume behaved as designed each time.
- 1 config fix in scratch WORKFLOW.md (max_turns >= active states validation caught it — good error).

## Verdict
GREEN — pipeline, DAG gating, contract enforcement, git automation (worktree wip commits,
preflight, merge), evidence vault, and wiki learning loop all function on the minimal branch.
Known friction: claude stream flakiness under nested sessions (auto-pause handles it); a
retryable-stream-error auto-retry policy is a candidate backlog item.
