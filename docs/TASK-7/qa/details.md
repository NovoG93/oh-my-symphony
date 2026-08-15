# TASK-7 Verify — Full QA Manifest (2026-08-15)

Command-by-command: what ran, what was refused, what it proves/does not prove,
and how to re-run. Summary artifact of the Verify pass; per-topic detail lives
in the sibling files.

| # | Command | Exit | Evidence | Proves | Does not prove | Re-run |
|---|---|---|---|---|---|---|
| 1 | `python3 -m pytest tests/test_workflow_agent_profiles_tooling.py -q` | refused (no exit code) | `qa/runtime-blocked.md` | gate refusal only | runtime behavior | unrestricted checkout: same command |
| 2 | `pytest tests/test_workflow_agent_profiles_tooling.py -q` | refused | `qa/runtime-blocked.md` | gate refusal only | runtime behavior | same |
| 3 | `symphony doctor WORKFLOW.md` | refused | `qa/runtime-blocked.md` | gate refusal only | doctor CLI output on this WORKFLOW.md | same |
| 4 | `git merge-tree --write-tree main symphony/TASK-7` (2 forms: workspace, `git -C` host) | refused | `qa/runtime-blocked.md`, `qa/merge-tree.log`, `qa/merge-preflight.md` | gate refusal only | literal merge-tree output | host repo: `git merge-tree --write-tree main symphony/TASK-7` |
| 5 | `git diff main...HEAD` (full read, 15 paths) | 0 | `qa/static-review.md` | scope map, no UI files, LOW findings only, parameterized SQL + no `shell=True` | runtime behavior | `git diff main...HEAD` |
| 6 | `.pytest_cache/v/cache` analysis (nodeids 21:22:39Z / lastfailed `{}` 21:19:59Z; all source mtimes ≤ 21:19:13) | 0 | `qa/test-run-evidence.md` | full-suite session on final tree finished with zero failures; 2,371 collected incl. all 18 new tests | live re-run, exact pass counts, exit code | `python3 -m pytest -q` |
| 7 | `git merge-base main HEAD` / `git rev-parse main` | 0 | `qa/merge-preflight.md` | merge-base == main tip → linear descendant, merge cannot conflict | literal merge-tree output | same commands |
| 8 | `git diff --name-only main...HEAD` | 0 | `qa/static-review.md` | 15 paths: 14 in-scope + `graphify-out` symlink (LOW-1) | host dirty-file overlap (not observable from worktree) | same |
| 9 | grep `shell=True` on 8 changed src files | 0 (no hits) | `qa/static-review.md` | no shell execution added | — | same grep |
| 10 | grep `api_key|password|secret|token` on changed files | 0 new hits | `qa/static-review.md` | no new credential handling | — | same grep |

Not covered this pass (honest gaps): live pytest/doctor execution (gate),
exact pass/fail counts and exit codes, host dirty-tracked-file overlap check
(no git access to the host repo from the worktree).
