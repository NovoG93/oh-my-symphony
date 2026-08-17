# TASK-12 Verify: pytest Cache Evidence (indirect run proof)

Live pytest execution is denied in this workspace (`qa/runtime-blocked.md`). The committed
worktree carries the last pytest cache; per TASK-4 precedent, `nodeids` is an append-only union
of all collected test ids and `lastfailed` records the most recent failing run's failures.

## Observed (2026-08-17, stat -c "%y %n")
- `.pytest_cache/v/cache/nodeids` — mtime **19:45:50Z** — 2466 collected node ids.
- `.pytest_cache/v/cache/lastfailed` — mtime **19:42:02Z** — exactly one entry:
  `tests/test_usage_limits.py::test_usage_cap_rejects_invalid_percent[80]`.

## Analysis
- nodeids contains **all 30 current** `test_usage_limits.py` ids (cache lines 1827-1860),
  including `..._rejects_invalid_percent["80"]` and `['70%']`, and **all 16**
  `test_workflow_agent_profiles.py` ids (lines 2156-2171). Both acceptance modules were
  collected in the latest collection run (<= 19:45:50Z).
- The sole `lastfailed` id `[80]` is the int-80 parametrization, which does **not** exist in the
  committed test file — the committed list parametrizes the YAML string `"80"` (nodeid `["80"]`).
  The recorded failure belongs to a stale test id that was replaced during implementation; it
  cannot fail again because it is no longer collected.

## What it proves / does not prove
- Proves: both test modules were collected as committed; no current test id appears in
  lastfailed; the only recorded failure targets a removed parametrization.
- Does not prove: a fresh green execution of the current tree (no pytest run was possible in
  this session). Consistent with, but not independently confirming, the implementation
  agent's recorded run in `docs/TASK-12/work/details.md` (30 + 16 passed; full suite
  2451 passed, 9 skipped in 169s).

## How to re-run (reviewer)
```
cd /home/symphony/symphony_workspaces/TASK-12
.venv/bin/python -m pytest tests/test_usage_limits.py tests/test_workflow_agent_profiles.py -q
.venv/bin/python -m pytest -q   # full suite
```
