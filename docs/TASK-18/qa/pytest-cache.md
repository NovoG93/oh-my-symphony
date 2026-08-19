# TASK-18 QA — Test-suite evidence (AC 5, indirect)

Live pytest was denied by the permission harness in Verify (`qa/runtime-blocked.md`).
This file records the indirect evidence from the worktree's `.pytest_cache` plus
the static cross-checks, and states exactly what each piece proves.

## Raw cache facts (2026-08-19)

- `.pytest_cache/v/cache/nodeids` — 2595 nodeids, mtime `16:16:24Z` (pre-commit;
  wip commit `795e70e` — originally `be10c9d`, re-committed by the host gate with
  the QA evidence docs; same tree for src/tests — was created 16:18:38Z).
- `.pytest_cache/v/cache/lastfailed` — content `{}`, mtime `16:13:30Z`.
- All 12 `tests/test_copilot_backend.py::*` nodeids are present (none in lastfailed).
- Copilot-related nodeids in the suite: 32 total — the 12 new file tests, 6
  `TestCopilotBackendContract` members, 3 `test_copilot_auth_*` doctor tests,
  usage-probe/exhaustion tests, and `[copilot]` parametrizations of protocol/git-grant/
  dispatch/tracker/board-cli tests.
- 4 files have mtimes after the last completed run: `pi.py` (16:16:46Z),
  `copilot.py` (16:16:42Z), `usage.py` (16:16:51Z), `tests/test_copilot_backend.py`
  (16:16:55Z). Every other file in the branch diff was final before the run.

## What the cache proves (mechanism verified in `.venv/.../_pytest/cacheprovider.py`)

- `nodeids` is written at every run's `sessionfinish` (cacheprovider NFPlugin, line
  469): the 16:16:24Z write is the END of a completed full-suite run.
- `lastfailed` is only rewritten when it changed during a run (line 422: write
  guarded by `saved_lastfailed != self.lastfailed`). It stayed `{}` across the
  16:16:24Z run ⇒ that run recorded **zero failures** (any failure would have
  mutated the in-memory dict and forced a rewrite with a newer mtime).
- Collection succeeded (no collection/import errors — those fail the run too), so
  every changed module imported cleanly in the recorded run.
- The recorded run covered the final committed content of all changed files except
  the 4 listed above, whose last edits landed 18–31 s after the run finished.

## What it does NOT prove

- The final micro-edits to `pi.py` / `copilot.py` / `usage.py` /
  `tests/test_copilot_backend.py` were not re-run. Mitigation: these exact files
  pass the static ACs in `qa/ac-static.md` and were re-reviewed line-by-line in
  Verify; a syntax re-check (`py_compile`) was attempted and denied
  (`qa/runtime-blocked.md` row 2).
- The precise "2,584 passed" figure: the cache stores only nodeids (cumulative,
  2595, includes one stale renamed nodeid `test_github_copilot_usage_probe_fails_open`
  that no longer exists in the committed tree) and `lastfailed` ({}). The figure is
  consistent with the recorded run but not independently confirmable from the cache.
- No live-copilot behavior (no real `copilot` binary/token used anywhere — suite
  uses `_FakeSubprocess` doubles; consistent with the ticket's Done Signal
  "live Copilot CLI execution Not proven").

## How to re-run

```bash
.venv/bin/pytest -q -p no:cacheprovider                 # full suite
.venv/bin/pytest tests/test_copilot_backend.py -q -p no:cacheprovider
```
