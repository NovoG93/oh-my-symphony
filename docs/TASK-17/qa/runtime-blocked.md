# TASK-17 Verify — Runtime Blocked Record

**What**: Which live commands the harness permission policy refused during Verify, and what replaces them.
**Why**: Keep the record honest: refused commands are "Not proven live", and each refusal gets an exact re-run command.
**As-Is -> To-Be**: Silent gap in live evidence -> Every refusal logged with its re-run command and its indirect substitute.

Refusals observed 2026-08-17 in the TASK-17 worktree (same policy shape as TASK-14/15/16 Verify):

| # | Command attempted | Result | Re-run command (outside worktree) |
|---|---|---|---|
| 1 | `.venv/bin/pytest tests/test_usage_limits.py tests/test_workflow_agent_profiles.py tests/test_orchestrator_usage_limits.py -q` | "This command requires approval" | same command; exit code 0 expected |
| 2 | `git merge-tree --write-tree develop symphony/TASK-17` | "This command requires approval" | run from the host repo; exit code 0 expected |
| 3 | `grep ... | sort | uniq -c | awk '{s+=$1} END {print s}'` (nodeid summing pipe) | "requires approval" (awk part) | plain grep counts used instead |

## Indirect substitutes used

- Live pytest -> `.pytest_cache/v/cache/` from the implementation turn's final full-suite run: `nodeids` (2564 entries, mtime **2026-08-17 21:54:37**) contains all 8 new Stage 6.2 test ids plus the 8-backend fail-open parametrizations; no `lastfailed` file exists, and per `_pytest/cacheprovider.py` (lines 415-423 in this venv's pytest) `lastfailed` is only written when the recorded failure set *changes* during a run — the last completed run therefore ended with **zero failures**. Details and caveats: `qa/test-cache-evidence.md`.
- merge-tree -> topology proof: `git merge-base develop symphony/TASK-17` = `115223c1ac0e10a4fcfb6d3135431deb3691a72e` = `git rev-parse develop`, i.e. the target tip is a strict ancestor of the feature branch, so a `--no-ff` merge applies zero target-side deltas and cannot conflict. See `qa/merge-preflight.md` (and the prescribed `qa/merge-tree.log`).

## Not proven

- Live re-run of any pytest suite in this Verify pass (usage suites, fail-open invariant, full suite).
- Live browser navigation (no UI source changed in this ticket; the repo's own e2e suites were green in the recorded run — `tests/test_web_browser_e2e.py` nodeids present, absent from lastfailed).
