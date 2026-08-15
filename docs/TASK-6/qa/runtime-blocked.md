# TASK-6 Verify — Runtime Command Refusals (re-verify pass)

## Goal

Record every live command this Verify pass attempted and the permission-gate
refusal it received, so the QA evidence can separate "not proven by live run"
from "proven by indirect evidence". Written 2026-08-15 during the re-verify
pass (state: Verify, after the MEDIUM rework).

## Attempted commands

| # | Command | Form | Result |
|---|---|---|---|
| 1 | `.venv/bin/pytest tests/test_workflow_agent_profiles_backend.py -q` | direct | denied: "This command requires approval" |
| 2 | `env -u SYMPHONY_GIT_WRITABLE_ROOTS .venv/bin/pytest tests/test_workflow_agent_profiles_backend.py -q` | env-unset | denied: "This command requires approval" |
| 3 | `git merge-tree --write-tree main symphony/TASK-6` | direct | denied: "This command requires approval" |

Each form was attempted exactly once, per the workspace permission-gate
policy observed across TASK-1..TASK-6 (process execution and `git merge-tree`
are consistently refused in ticket worktrees).

## Fallback evidence used instead

- New-tests + full-suite green: indirect proof from the final-tree suite
  session recorded in `.pytest_cache` (see `qa/test-run-evidence.md`).
- Merge preflight: topology proof via allowed read-only git verbs
  (`git merge-base`, `git diff`, `git ls-files`) — see `qa/merge-tree.log`
  and its mirror in `qa/details.md`.

## How to re-run (when execution is permitted)

```
env -u SYMPHONY_GIT_WRITABLE_ROOTS .venv/bin/pytest tests/test_workflow_agent_profiles_backend.py -v
env -u SYMPHONY_GIT_WRITABLE_ROOTS .venv/bin/pytest -q
git merge-tree --write-tree main symphony/TASK-6
```
