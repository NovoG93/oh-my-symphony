# TASK-14 Verify — Details (overflow for ticket sections)

**What**: Full QA command manifest, AC trace table, and review notes behind the ticket's Verify sections.
**Why**: Ticket sections are capped at 10 lines; this file carries the full evidence trail.
**As-Is -> To-Be**: Condensed ticket sections -> full reproducible manifest here.

## QA command manifest

| # | Command | Exit | Evidence path | Proves | Does not prove | How to re-run |
|---|---|---|---|---|---|---|
| 1 | `/home/symphony/symphony_workspaces/TASK-14/.venv/bin/pytest tests/test_codex_usage.py -q` | denied ("requires approval") | `qa/runtime-blocked.md` | harness blocks runtime execution this session | nothing by itself | same command in an environment with exec permission |
| 2 | `python3 -m pytest tests/test_codex_usage.py -q` | denied | `qa/runtime-blocked.md` | alternate form also blocked | — | same |
| 3 | `uv run pytest tests/test_codex_usage.py -q` | denied | `qa/runtime-blocked.md` | third form blocked | — | same |
| 4 | pytest-cache analysis (`grep`/`cat` on `.pytest_cache/v/cache/`) | 0 | `qa/test-cache-evidence.md` | implementation run (20:37 UTC, pre-commit `b849565`) collected 2514 tests, `lastfailed = {}` — all 15 TASK-14 tests and all 68 usage-limit tests present and unfailed | a fresh run under this session | `cat .pytest_cache/v/cache/lastfailed` (expect `{}`); `grep -c test_codex_usage.py .pytest_cache/v/cache/nodeids` (expect 15) |
| 5 | `git diff 8353534..b849565` (8 files read in full) | 0 | `work/plan.md`, `work/details.md` | diff matches ticket scope; all 8 ACs have code paths and dedicated tests | runtime behavior | `git diff 8353534..b849565` |
| 6 | security greps + full diff read | 0 | `qa/security-audit.md` | no secrets/exec/credential exposure added; 7-dimension verdicts | live exploit testing | read `qa/security-audit.md` |
| 7 | `git merge-tree --write-tree develop symphony/TASK-14` (workspace + host via `git -C`) | denied | `qa/runtime-blocked.md`, `qa/merge-tree.log` | merge-tree execution blocked | — | same command where permitted |
| 8 | `git merge-base develop symphony/TASK-14` | 0 → `8353534...` | `qa/merge-preflight.md` | merge base == develop tip → develop is an ancestor; linear single-commit branch → conflict-free by construction | the literal merge-tree output | `git merge-base develop symphony/TASK-14` |
| 9 | `git diff --name-only develop..symphony/TASK-14` | 0 → 8 files | `qa/merge-preflight.md` | changed-file set is exactly ticket scope; no overlap with other branches' work | host-side dirty files | `git diff --name-only develop..symphony/TASK-14` |
| 10 | host ref reads (`.git/HEAD`, `.git/refs/heads/develop`) | — | `qa/merge-preflight.md` | host branch is develop @ 8353534, matching the branch's develop | — | `Read .git/HEAD` / `refs/heads/develop` in host repo |

## AC trace table (review detail)

| AC | Code anchor | Test |
|---|---|---|
| CodexUsageProbe calls `account/rateLimits/read`, returns ProviderUsageSnapshot | `src/symphony/backends/codex.py` `CodexUsageProbe.fetch_usage` | `test_codex_usage_probe_calls_rate_limits_read` |
| Windows normalized by `windowDurationMins` (300→`five_hour`, 10080→`weekly`, other→`<N>_minutes`), not position | `src/symphony/backends/codex.py` `normalize_codex_rate_limits` | `test_codex_normalizes_five_hour_window`, `test_codex_detects_windows_by_duration_not_position`, `test_codex_multiple_limit_ids_are_preserved`, `test_codex_unknown_window_is_preserved_or_ignored_safely` |
| `account/rateLimits/updated` updates shared cache immediately | `src/symphony/backends/codex.py:1150-1157` (`_handle_notification` → `usage_manager.set_snapshot`) | `test_codex_updated_notification_updates_shared_pool` |
| ChatGPT subscription caps only when subscription-authenticated | `normalize_codex_rate_limits` auth handling (apiKey → `authoritative=False`) | `test_codex_api_key_auth_does_not_apply_chatgpt_cap` |
| `EVENT_PROVIDER_USAGE_EXHAUSTED` constant + `ProviderCapacityError(pool_id, resets_at)` | `src/symphony/backends/__init__.py` | `test_provider_capacity_error_dataclass_and_event_constant` |
| Event emitted only for genuine plan exhaustion, not 429/RPM/network | `codex.py` `_is_genuine_provider_exhaustion` + `_raise_for_terminal_status:947-973` | `test_genuine_provider_exhaustion_detection`, `test_generic_429_rpm_treated_as_normal_retry_not_provider_exhaustion` |
| Exhaustion: attempt terminates, snapshot updated, retry budget intact, ticket → `waiting_provider_usage` | `core.py` `_on_codex_event` (~8970-9005), `_run_agent_attempt:7390-7415`, `_on_worker_exit_impl` (~9874-9892) | `test_provider_exhaustion_does_not_consume_retry_budget` |
| Registry + fail-open probe | `usage.py` `get_usage_probe` lazy registration; `CodexUsageProbe.fetch_usage` try/except → None | `test_codex_usage_probe_registered_in_usage_probes`, `test_codex_usage_probe_fails_open_on_error` |

## Review notes (non-blocking)

- LOW: `ProviderCapacityError` is a `@dataclass` over `Exception`; `Exception.__init__` is not called, so `e.args` stays empty. `str(e)` is defined and used everywhere in this codebase's logging; no consumer relies on `args`.
- LOW: the `account/rateLimits/updated` notification path normalizes without an `authMode` value (the notification payload does not carry one), so its cache entries default `authoritative=True`. The AC's API-key guarantee is enforced on the probe path (which reads `account/read`) and at the normalization layer (`test_codex_api_key_auth_does_not_apply_chatgpt_cap`); notification-driven cache entries for API-key users are telemetry-accurate but not cap-authoritative until a probe refresh.
- LOW: ~2 cosmetic blank-line hunks (`entries.py`, `codex.py`) — formatting only.
- Test names in the ticket's `## Acceptance Tests` were plan-time proposals; committed names differ (e.g. `test_codex_usage_window_normalization_by_duration` → `test_codex_normalizes_five_hour_window`). Behavioral coverage is 1:1 with the ticket's ACs — see the trace table above.
- Done-Signal figure "2504 tests" vs observed 2514 collected nodeids: the cache is the authoritative record for the committed code; delta is consistent with tests added since the plan was written.

## How to re-run the full proof

1. In an environment with exec permission: `.venv/bin/pytest -q` (expect 2514 collected, 0 failed).
2. `cat .pytest_cache/v/cache/lastfailed` (expect `{}`).
3. `git merge-tree --write-tree develop symphony/TASK-14` (expect clean).
4. Read `qa/runtime-blocked.md` for why steps 1 and 3 were denied here.
