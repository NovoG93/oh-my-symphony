# TASK-16 Verify — Static Contract Verification (Stage 6.12)

**What**: Acceptance criterion by acceptance criterion, what was checked, how, and what the proof is.
**Why**: Live pytest is denied in this worktree, so each AC is verified statically against the code and by the last full-suite run's pytest cache.
**As-Is -> To-Be**: Assertion of green tests only -> Per-AC proof: where the code is, what the test asserts, and whether the last suite run confirms it.

## Full-suite cache evidence (the indirect runtime proof)

- `.pytest_cache/v/cache/nodeids` — 2557 collected tests, mtime 2026-08-17 21:34 (after the implementation commit, clean tree).
- `.pytest_cache/v/cache/lastfailed` — `{}`, mtime 2026-08-17 21:31. A green run writes the empty failure map at session end.
- The 8 new Stage 6.12 nodeids are all present and absent from `lastfailed`:
  - `tests/test_webapi.py::test_workflow_api_exposes_usage_pools`
  - `tests/test_webapi.py::test_workflow_api_exposes_configured_usage_pools_content`
  - `tests/test_webapi.py::test_snapshot_exposes_provider_usage`
  - `tests/test_webapi.py::test_remaining_percent_is_100_minus_used_percent`
  - `tests/test_web_static_contract.py::test_provider_usage_card_exists`
  - `tests/test_web_static_contract.py::test_waiting_provider_usage_has_translation`
  - `tests/test_web_static_contract.py::test_usage_unknown_is_rendered_without_error`
  - `tests/test_web_static_contract.py::test_estimated_usage_is_visually_distinguished`
- The two modified pre-existing tests (`test_open_project_starts_only_destination_and_returns_independent_url`, `test_board_request_view_ships_accessible_explainable_schedule_contract`) are likewise in nodeids and absent from `lastfailed`.
- Browser e2e suite (`tests/test_web_browser_e2e.py`, 5 nodeids incl. `test_web_board_browser_e2e`) also ran green in that run; playwright is installed in `.venv`, and a missing chromium binary would have failed the run, so chromium was present.
- Caveat: nodeids/lastfailed mtimes differ by 3 minutes; the cache proves the implementation turn's final run, not a fresh run inside this Verify pass. It does not prove individual test durations, ordering, or that nothing was skipped silently (no skip markers exist on the new tests; `importorskip` applies only to the e2e module where the module IS installed).

## AC-by-AC static verification

### AC1 — snapshot exposes per-pool `provider_usage` (source, windows, status, stale, authoritative)
- `src/symphony/orchestrator/core.py:2883` adds `"provider_usage": self._provider_usage_projection()` to `snapshot()`.
- Projection (core.py:2891-2969) emits exactly `source`, `windows` (`used_percent`, `remaining_percent`, `resets_at`), `status` (`available`/`capacity_paused`/`unavailable`), `stale`, `authoritative` per pool.
- `test_snapshot_exposes_provider_usage` asserts all of these against a real `Orchestrator` with an injected `ProviderUsageManager` — passed in last run (cache).
- `resets_at` ISO-8601 conversion asserted (`"2026-08-17T23:00:00+00:00"`).

### AC2 — workflow payload exposes configured `usage_pools` (source + caps)
- `src/symphony/webapi.py:748-754` serializes `{name: {"source", "caps"}}` in `_workflow_payload`.
- `test_workflow_api_exposes_usage_pools` (shape) and `test_workflow_api_exposes_configured_usage_pools_content` (values: `codex` → source `codex`, caps `{"five_hour": 80.0, "weekly": 70.0}`) — both green per cache.

### AC3 — Provider Usage card near the Agent Policy area, bars/cap/remaining/reset; blocked shows 'Capacity paused' + 'Available after'
- `buildProviderUsageCard` at `src/symphony/web/static/app.js:2814`; mounted immediately after `buildAgentPolicyCard` in the workflow editor (app.js:2699) and on Settings (app.js:4881); card id `provider-usage-card`.
- Paused state: `chip-status--paused` badge with `t('usage.capacityPaused')` ("Capacity paused"), `.usage-paused-notice` with `t('usage.tasksPaused')` + `t('usage.waitingForCapacity')`, and `t('usage.availableAfter')` in the meta row when `isPaused` (app.js:2861-2872, 2947-2950).
- `test_provider_usage_card_exists` asserts the function, the id, the CSS classes, and the EN/KO "Capacity paused"/"Available after" strings — green per cache.

### AC4 — `waiting_provider_usage` in the schedule-reason map
- `src/symphony/web/static/app.js:1336`: `waiting_provider_usage: t('schedule.reasonProviderUsage')`.
- `src/symphony/webapi.py:392`: backend public reason `"waiting_provider_usage": "waiting for provider capacity"`.
- `test_waiting_provider_usage_has_translation` asserts the map entry and both i18n strings — green per cache.

### AC5 — i18n labels (EN + KO)
- All required keys present: EN block i18n.js:537-560, KO block i18n.js:1114-1137 — Provider Usage / Usage pool / 5-hour / Weekly / Daily / Monthly / Remaining / Configured cap / Usage unavailable / stale / Waiting for provider capacity / Resets at / Available after / Estimated / Authoritative (+ Capacity paused, Available, tasks-paused/percent helpers). 21 keys per language, pairwise identical key sets (parity verified by direct comparison of the two blocks).
- `scripts/check_i18n.py` (EN-as-source-of-truth parity + every `t('key')` has an entry) would pass: all 21 keys are referenced from app.js and present in both dictionaries.

### AC6 — style.css styling (bars, badges, stale/estimated indicators)
- `src/symphony/web/static/style.css:2217-2245`: `.provider-usage-card`, `.usage-bar-track`, `.usage-bar-fill`, `--paused`, `--estimated`, `.chip-stale`, `.chip-estimated`, badges, meta rows.
- `test_estimated_usage_is_visually_distinguished` asserts estimated classes in both JS and CSS — green per cache.

### AC7 — Stage 6.12 contract tests green
- All 8 tests present in nodeids, zero in lastfailed (see cache evidence above). Result: pass with the caveat that this pass could not re-execute them (see `qa/runtime-blocked.md`).

## Done Signals cross-check
- `GET /api/v1/workflow` returns `usage_pools` ✓ (AC2). `orchestrator.snapshot()` includes `provider_usage` ✓ (AC1). Card renders bars/badges/remaining/reset ✓ (AC3). Schedule reason localized EN+KO ✓ (AC4/AC5). Suite green per cache ✓ (AC7). Not proven: live external provider polling (offline/mocked by design) and a fresh live run inside Verify.

## How to re-run
```
.venv/bin/py.test tests/test_webapi.py tests/test_web_static_contract.py -k "usage_pools or provider_usage or waiting_provider_usage or usage_unknown or estimated_usage or remaining_percent_is_100" -q   # expect 8 passed
.venv/bin/python scripts/check_i18n.py   # expect exit 0
.venv/bin/py.test -q                     # expect 2557 passed, 0 failed
```
