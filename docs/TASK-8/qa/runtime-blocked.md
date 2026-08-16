# TASK-8 Verify — Runtime Command Refusals (2026-08-16, Verify pass)

The workspace permission policy refuses all process/network execution from the
ticket worktree. Each runtime command was attempted at most once per form and
refused with "This command requires approval" (no exit code, nothing executed).

| # | Command (attempted once) | Result |
|---|---|---|
| 1 | `.venv/bin/pytest tests/test_workflow_agent_profiles_e2e.py -v --no-header -p no:cacheprovider` | refused by permission gate |
| 2 | `.venv/bin/pytest -q --no-header -p no:cacheprovider` (full suite) | refused by permission gate |
| 3 | `symphony doctor WORKFLOW.md --workspace .` | refused by permission gate |
| 4 | `git merge-tree --write-tree main symphony/TASK-8` (workspace cwd) | refused by permission gate |
| 5 | `git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree main symphony/TASK-8` (host repo) | refused by permission gate |

Consequence for evidence: live re-runs are Not proven this pass. Proof instead
comes from (a) the recorded pytest session in `.pytest_cache/v/cache/`
(see `test-run-evidence.md`) and (b) read-only git topology checks
(see `merge-preflight.md`).

How to re-run on an unrestricted machine (host repo, after merge):
- `.venv/bin/pytest tests/test_workflow_agent_profiles_e2e.py -v`
- `.venv/bin/pytest -q` (full suite)
- `symphony doctor WORKFLOW.md`
- `git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree main symphony/TASK-8`
