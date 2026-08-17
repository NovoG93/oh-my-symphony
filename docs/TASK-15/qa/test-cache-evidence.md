# Test-Cache Evidence: pytest run record (Verify, 2026-08-17)

**What**: Analysis of the worktree's `.pytest_cache` from the implementation turn.
**Why**: Live pytest is denied by the permission policy; the cache is the durable record of the most recent suite runs.
**As-Is -> To-Be**: No run evidence -> Counts + lastfailed state mirror, with explicit caveats.

## Raw data (mirrored from `.pytest_cache/v/cache/`; the cache dir is git-ignored)

- `nodeids` — 233,972 bytes, mtime **2026-08-17 21:05**, contains **2549** collected test ids.
- `lastfailed` — content `{}`, mtime **2026-08-17 21:04**.
- Working tree committed by the harness at **2026-08-17 21:06** (`4d3b1a1 wip`); the working tree is clean, so the committed code equals the code these runs executed.

## Counts relevant to the Done Signals

- `tests/test_backend_usage_probes.py` nodeids: **36** — the exact 36 acceptance tests of this ticket (28 named + 8 parametrized `test_usage_probe_failure_never_prevents_dispatch[...]` across codex/claude/agy/gemini/kiro/opencode/pi/prime-agent).
- Other usage-suite nodeids (`test_codex_usage.py`, `test_usage_limits.py`, `test_orchestrator_usage_limits.py`): **83** -> 36 + 83 = **119**, matching Done Signal 2.
- Pyright gate nodeids: 4 (`tests/test_pyright_wrapper.py` x3 + `tests/test_package_metadata.py::test_pyright_wrapper_console_script_is_declared`), none in `lastfailed` -> the type-check wrapper, which invokes the real `symphony-pyright` over `src/` (pyproject gates `include = ["src"]`), passed in that run -> Done Signal 3.
- `lastfailed = {}` -> the last **completed** run ended with **zero failures** -> Done Signal 4 (full suite clean) as recorded.

## What this proves / does not prove

- **Proves**: every one of the 36 acceptance tests was collected and executed in a run that ended with an empty failure set; the same run covered the whole 2549-test suite and the pyright gate; no test id from the four usage files appears in `lastfailed`.
- **Does not prove**: a *fresh* re-run on the exact committed SHA. `nodeids` mtime (21:05) is newer than `lastfailed` (21:04), so the clean-completion record is the ~21:04 run; the later collection pass either was interrupted before session-finish or was a collect-only check. Any edits between the 21:04 run and the 21:06 commit would not be covered by a full green run. The gap is unprovable from the cache alone (see `qa/static-review.md` for the compensating full-diff read).
- Fresh re-run was denied 3x -> `qa/runtime-blocked.md`.

**How to re-run**: `python -m pytest -q` in a checkout of `symphony/TASK-15` (see `qa/runtime-blocked.md`).
