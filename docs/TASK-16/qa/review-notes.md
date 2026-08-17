# TASK-16 Verify — Diff Review Notes

**What**: Reviewer's walk of the full TASK-16 diff against the ticket, plan, ACs, and Done Signals.
**Why**: Confirm every change serves a Stage 5 deliverable and nothing extra (or harmful) rides along.
**As-Is -> To-Be**: Unreviewed branch delta -> Delta accounted for per file, with severity-rated findings.

## Scope vs. ACs (delta = `develop..symphony/TASK-16`, merge base `01f3a41`)

| File | Delta | AC covered |
|---|---|---|
| `src/symphony/orchestrator/core.py` | +89 | AC1 — `provider_usage` in snapshot (core.py:2883, projection at 2891) |
| `src/symphony/webapi.py` | +12 | AC2 — `usage_pools` in workflow payload (webapi.py:748); `waiting_provider_usage` schedule reason (webapi.py:392); board payload (webapi.py:1170) |
| `src/symphony/web/static/app.js` | +163 | AC3/AC4 — `buildProviderUsageCard` (app.js:2814), schedule-reason map (app.js:1336), mounts at 2699/4881 |
| `src/symphony/web/static/i18n.js` | +52 | AC5 — EN + KO label sets |
| `src/symphony/web/static/style.css` | +28 | AC6 — bars, badges, stale/estimated indicators (style.css:2217) |
| `tests/test_webapi.py` | +168 | AC1/AC2/AC6 tests (Stage 6.12) |
| `tests/test_web_static_contract.py` | +54 | AC3/AC4/AC5/AC7 contract tests (Stage 6.12) |
| `docs/TASK-16/work/*.md` | +93 | Ticket evidence |

## Verification of the projection logic (core.py:2891)

- Sources: pools come from `cfg.usage_pools` keys + `_usage_manager.snapshots` keys; snapshots fetched via the existing manager API — no new I/O.
- `remaining_percent = 100 - used_percent` fallback implemented at core.py:2907-2913; a Stage 6.12 test covers it.
- `resets_at` datetimes converted to ISO 8601; other values pass through (JSON-safe).
- Status derivation: `hard_limit_reached` → `capacity_paused`, else `evaluate()` == `WAIT_PROVIDER_USAGE` → `capacity_paused`, else `available`; no snapshot → `unavailable`. `evaluate()` on a missing snapshot returns READY per pre-existing `tests/test_usage_limits.py` (fail-open), so no raise path.
- `authoritative` defaults to True when no snapshot exists — matches the fail-open invariant (unknown usage must not block the UI or scheduler).

## Findings (all LOW; none CRITICAL/HIGH/MEDIUM)

1. **LOW — out-of-scope assertion removal**: `tests/test_web_static_contract.py::test_board_request_view_ships_accessible_explainable_schedule_contract` drops `assert "파일 보드에서만 지원" in js`. The string still exists (`src/symphony/web/static/i18n.js:771` contains it as a substring of the full sentence), so the assertion would pass at HEAD — the removal is unnecessary coverage loss, not a fix. No failure is hidden.
2. **LOW — no-op test rewrite**: `test_open_project_starts_only_destination_and_returns_independent_url` changed `client.server.app is not None` to `getattr(client.server, "app", None) is not None`. Installed aiohttp 3.14.3 still defines the `TestServer.app` property (`.venv/.../aiohttp/test_utils.py:327`), so the two forms are semantically identical. Harmless but unrelated to the ticket.
3. **LOW — dead code**: `api.getState` (app.js:60) targets `/state`, a route that does not exist in webapi.py; it is never called. The workflow-editor mount reads `state.status` (app.js:2698), which is never assigned; the guard short-circuits so no error occurs.
4. **Cosmetic**: two extra blank lines added in webapi.py. Ruff's selected rules are `["E4","E7","E9","F"]` (pyproject.toml:54) — E303 is not selected, so lint is unaffected.

## Cross-cutting safety checks

- `el()` renders string children via `document.createTextNode` (app.js:252) — no `innerHTML` anywhere in the delta; interpolated values (`{pool}`, `{n}`, `{error}`) cannot inject markup.
- `state.workflow` is already dereferenced by `buildAgentPolicyCard(state.workflow.agent)` on the line before the card mount, so the new mount cannot be the first failure point in that path.
- `renderSettingsPage` destructures `board` from `Promise.all` (app.js:4871) — the bare `board` identifier in the settings mount is defined in scope.
- `handle_board` uses `.get("provider_usage", {})` (webapi.py:1170), so stubs whose `snapshot()` lacks the key (e.g. the browser-e2e `_StubOrchestrator`) still work.
- `cfg.usage_pools` defaults to an empty dict on `ServiceConfig` (Stage 1), so `_workflow_payload` cannot KeyError on configs without pools.
- `_StubOrchestrator.snapshot` in tests/test_webapi.py gained `"provider_usage": {}` to mirror the real payload — in scope.

## Verdict

Every source change maps to a Stage 5 deliverable; findings are LOW severity (2 drive-by test edits, 1 dead code path). No rewind warranted.
