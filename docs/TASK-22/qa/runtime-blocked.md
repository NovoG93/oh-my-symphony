# TASK-22 Verify rewind — runtime commands refused (2026-08-19)

**What**: Record of runtime commands the sandbox permission gate refused during the Verify contract-completion turn.
**Why**: Honest audit trail: evidence gaps are environment limits, not test results; keeps "Not proven" claims precise.
**As-Is -> To-Be**: Refused commands unrecorded -> one line per refused command with fallback evidence.

## Refusals this turn (2026-08-19T19:24Z)

1. `git merge-tree --write-tree develop symphony/TASK-22` — "This command requires approval". Attempted once per form.
   - Fallback: recorded successful runs in `qa/merge-tree.md` (tree `1ddbc682…`, exit 0, at branch tip `8aefcb4`) + topology proof via allowed read-only verbs: `git rev-parse develop` == `git merge-base develop HEAD` == `fe68d355…` (zero divergence -> conflicts impossible).
   - Re-run outside the sandbox: `cd /home/symphony/symphony_workspaces/TASK-22 && git merge-tree --write-tree develop symphony/TASK-22`

## Accepted this turn

- `cmp copilot-smoke.txt docs/TASK-22/work/expected-ok.txt` — exit 0 (byte-identical to `OK\n`).
- `wc -l -c copilot-smoke.txt` — `1 3 copilot-smoke.txt` (1 line, 3 bytes).
- `git show HEAD:copilot-smoke.txt` — `OK` (committed content).
- Read-only git verbs (`git rev-parse`, `git merge-base`, `git diff`, `git log`) — all permitted.

## What this proves / does not prove

- Proves: the deliverable and its ACs were re-verified fresh this turn; the recorded merge-tree run sits at the current branch tip.
- Does not prove: a fresh live `merge-tree` execution this turn (denied); post-merge state on `develop` (orchestrator merge at `Done`).
