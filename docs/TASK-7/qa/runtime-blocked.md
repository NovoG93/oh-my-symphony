# TASK-7 Verify — Runtime Command Refusals (2026-08-15, Verify pass)

The workspace permission policy refuses all process/network execution from the
ticket worktree. Each runtime command was attempted at most once per form and
refused with "This command requires approval" (no exit code, nothing executed).

| # | Command (attempted once) | Result |
|---|---|---|
| 1 | `python3 -m pytest tests/test_workflow_agent_profiles_tooling.py -q` | refused by permission gate |
| 2 | `pytest tests/test_workflow_agent_profiles_tooling.py -q` | refused by permission gate |
| 3 | `symphony doctor WORKFLOW.md` | refused by permission gate |
| 4 | `git merge-tree --write-tree main symphony/TASK-7` (workspace cwd) | refused by permission gate |
| 5 | `git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree main symphony/TASK-7` (host repo) | refused by permission gate |

Consequence for evidence: live re-runs are Not proven this pass. Proof instead
comes from (a) the recorded full-suite pytest session in `.pytest_cache`
(see `test-run-evidence.md`) and (b) read-only git topology checks
(see `merge-preflight.md`).

How to re-run on an unrestricted machine (host repo, after merge):
- `python3 -m pytest tests/test_workflow_agent_profiles_tooling.py -q`
- `python3 -m pytest -q` (full suite)
- `symphony doctor WORKFLOW.md`
- `git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree main symphony/TASK-7`
