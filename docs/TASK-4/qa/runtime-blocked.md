# TASK-4 QA — Runtime commands blocked by workspace permission policy

Updated 2026-08-15T18:50Z, Verify stage, retry attempt 1. Branch `symphony/TASK-4`
@ 050d96f (fix commits for the MEDIUM duplicate-name finding applied).

The workspace permission policy denies process/network execution from ticket
worktrees (consistent with prior tickets TASK-1/TASK-2 and the 18:36Z pass).
Each command form was attempted once per pass; all returned "This command
requires approval" and were declined.

| # | Command (exact) | Purpose | Result | Pass |
|---|---|---|---|---|
| 1 | `pytest tests/test_workflow_agent_profiles.py -q` | Run the 16 profile unit tests | denied | 18:36Z |
| 2 | `python3 -m pytest tests/test_workflow_agent_profiles.py -q` | Same, via `python -m` form | denied | 18:36Z |
| 3 | `git merge-tree --write-tree main symphony/TASK-4` | Merge preflight per Verify gate | denied | 18:36Z |
| 4 | `pytest tests/test_workflow_agent_profiles.py -q` | Re-run after MEDIUM fix | denied | 18:50Z |
| 5 | `python3 -m pytest tests/test_workflow_agent_profiles.py -q` | Same, via `python -m` form | denied | 18:50Z |
| 6 | `git merge-tree --write-tree main symphony/TASK-4` | Merge preflight after MEDIUM fix | denied | 18:50Z |
| 7 | `git -C /home/symphony/git/oh-my-symphony status --porcelain` | Host dirty-file overlap check | denied | 18:50Z |

Consequences for this Verify pass:

- **Test execution**: Not proven by live run. Indirect evidence accepted:
  the `.pytest_cache` written by the 18:42Z full-suite run on this branch
  collected 15 of the 16 profile tests and `lastfailed` contains zero
  profile-test entries; the 16th (duplicate rejection) has never been
  executed and rests on static review only (see `qa/qa-evidence.md`).
- **Merge preflight**: `git merge-tree` unavailable. Topology fallback
  (allowed read-only git verbs) in `qa/merge-preflight.md`:
  `git merge-base main HEAD` = 4c9e7b1; branch-only files and main-only
  files since the fork are disjoint, so no textual conflict is expected, but
  a clean `git merge-tree` result is **Not proven**.
- **Host dirty check**: `git -C` denied; host worktree dirty state not
  readable from the ticket worktree. Not proven.

How to re-run (in an environment where execution is allowed):
`pytest tests/test_workflow_agent_profiles.py -q`,
`pytest -q` (full suite), and
`git merge-tree --write-tree main symphony/TASK-4`
