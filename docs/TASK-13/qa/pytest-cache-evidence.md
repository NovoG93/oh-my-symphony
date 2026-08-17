# pytest cache evidence — TASK-13 Verify (2026-08-17)

Source: `.pytest_cache/v/cache/` in the TASK-13 worktree (pytest writes
`nodeids` at session start and `lastfailed` at session end).

## Observed state

- `nodeids` — 2498 entries, mtime 2026-08-17 20:12; first entries are
  `tests/skills/test_skill_frontmatter.py::*`, i.e. the last collection was a
  FULL-SUITE run, not a targeted one.
- `lastfailed` — content `{}`, mtime 2026-08-17 20:11: zero failed tests
  recorded in the last COMPLETED session.
- Usage-limit test nodeids present in the collection:
  - `tests/test_orchestrator_usage_limits.py` — 27 ids (16 test functions;
    `test_any_configured_window_can_block` ×5 params, `test_usage_probe_failure_never_prevents_dispatch` ×8 params)
  - `tests/test_usage_limits.py` — 41 ids (includes the 30 Phase-1 tests from
    TASK-12 plus the 11 new `test_provider_usage_manager_*` tests)
  - 27 + 41 = 68, matching the "Expected: 68 passed" figure recorded in
    `docs/TASK-13/reproduce/eval.md`.

## Interpretation

The mtime pair (nodeids 20:12 newer than lastfailed 20:11) means a full-suite
run was started after a completed one, and had not finished when the turn
ended (wip commit d250c37 at 20:13:08Z). The completed 20:11 run recorded
`lastfailed = {}`.

## What this proves

- A full-suite pytest collection on this branch includes all 68 usage-limit
  tests (both files), i.e. the new tests are importable, collectible, and
  correctly parametrized.
- The most recent COMPLETED full-suite session recorded zero failures.
- No test id from either usage-limit file appears in `lastfailed`.

## What this does NOT prove

- That the completed 20:11 run itself collected these exact 68 ids (its
  nodeids file was overwritten by the later collection) — though the files
  were in final content by then and pytest collects everything under tests/.
- Exit codes, pass counts, or durations; `lastfailed={}` is failure-absence
  evidence, not a pass counter.
- Anything about pyright / ruff (separate tools, separate cache if any).

## How to re-run

`cd /home/symphony/symphony_workspaces/TASK-13 && .venv/bin/pytest -q`
then inspect `.pytest_cache/v/cache/lastfailed` (empty = no failures).
Live execution is denied in this worktree; see `qa/runtime-blocked.md`.
