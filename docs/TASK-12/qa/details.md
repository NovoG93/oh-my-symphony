# TASK-12 Verify: QA Details (overflow for ## QA Evidence / ## AC Scorecard)

## Full command manifest

| # | Command | Exit | Evidence | Proves | Does not prove |
|---|---|---|---|---|---|
| 1 | `git diff develop..HEAD > docs/TASK-12/qa/diff.md` | 0 | `qa/diff.md` | Exact reviewed change set: 9 files, +604/-3 | Runtime behavior |
| 2 | `grep -c "^def test_" tests/test_usage_limits.py` / `.../test_workflow_agent_profiles.py` | 0 | `qa/test-inventory.md` | 20 functions (1 parametrized x11 = 30 tests) + 16 tests committed | Test pass/fail outcome |
| 3 | `stat` of `.pytest_cache/v/cache/{nodeids,lastfailed}` + grep of nodeids | 0 | `qa/pytest-cache-evidence.md` | Latest collection (19:45:50Z) includes all 46 acceptance ids; sole recorded failure (19:42:02Z) is stale param `[80]` absent from committed tests | Fresh green run of current tree |
| 4 | `grep -nE '^\+.*(api_key\|...)' qa/diff.md` (3 patterns) | 0 | `qa/security-static.md` | No secrets / exec constructs / network access in added lines | Security of pre-existing code |
| 5 | `git rev-parse develop HEAD`; `git merge-base develop HEAD` | 0 | `qa/merge-tree.md` | develop tip == merge-base -> conflict-free fast-forward topology | — |
| 6 | `.venv/bin/python -m pytest ...`; `pytest ...`; `.venv/bin/pyright src tests`; `git merge-tree --write-tree develop symphony/TASK-12`; `git check-ignore -v ...` | — | `qa/runtime-blocked.md` | All denied by permission policy (each form attempted once) | Any runtime result |

Not proven by fresh re-run (denied): Stage 6.1 pytest green, pyright 0/0, full suite
2451 passed / 9 skipped. Recorded by the implementation agent in
`docs/TASK-12/work/details.md`; indirect cache support in `qa/pytest-cache-evidence.md`.

## How to re-run everything
```
cd /home/symphony/symphony_workspaces/TASK-12
.venv/bin/python -m pytest tests/test_usage_limits.py tests/test_workflow_agent_profiles.py -q
.venv/bin/pyright src tests
.venv/bin/python -m pytest -q
```
