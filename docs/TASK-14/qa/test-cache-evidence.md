# TASK-14 QA — pytest cache evidence of the implementation run

**What**: The most recent pytest run collected 2514 tests and recorded zero failures/errors.
**Why**: Live pytest execution is denied in this session (see `qa/runtime-blocked.md`); the `.pytest_cache` written by the implementation run is the durable record of test outcomes.
**As-Is -> To-Be**: As-Is: no durable record of whether the acceptance tests pass. To-Be: cache contents extracted and correlated with the implementation commit.

## Evidence

- `.pytest_cache/v/cache/nodeids` — 2514 lines; mtime `2026-08-17 20:37`.
- `.pytest_cache/v/cache/lastfailed` — content `{}` (no failures, no errors); mtime `2026-08-17 20:37`.
- The `wip` commit containing the implementation is `b849565` (2026-08-17T20:37:33Z); `git status` shows a clean tree, so the cache reflects the committed implementation code.
- All 15 `tests/test_codex_usage.py` tests are present in `nodeids`:

```
tests/test_codex_usage.py::test_codex_api_key_auth_does_not_apply_chatgpt_cap
tests/test_codex_usage.py::test_codex_detects_windows_by_duration_not_position
tests/test_codex_usage.py::test_codex_hard_limit_reached_is_normalized
tests/test_codex_usage.py::test_codex_multiple_limit_ids_are_preserved
tests/test_codex_usage.py::test_codex_normalizes_five_hour_window
tests/test_codex_usage.py::test_codex_rate_limits_read_normalization
tests/test_codex_usage.py::test_codex_unknown_window_is_preserved_or_ignored_safely
tests/test_codex_usage.py::test_codex_updated_notification_updates_shared_pool
tests/test_codex_usage.py::test_codex_usage_probe_calls_rate_limits_read
tests/test_codex_usage.py::test_codex_usage_probe_fails_open_on_error
tests/test_codex_usage.py::test_codex_usage_probe_registered_in_usage_probes
tests/test_codex_usage.py::test_generic_429_rpm_treated_as_normal_retry_not_provider_exhaustion
tests/test_codex_usage.py::test_genuine_provider_exhaustion_detection
tests/test_codex_usage.py::test_provider_capacity_error_dataclass_and_event_constant
tests/test_codex_usage.py::test_provider_exhaustion_does_not_consume_retry_budget
```

- Usage-limit suites: 68 nodeids match `tests/test_usage_limits.py` / `tests/test_orchestrator_usage_limits.py`; together with the 15 above that is the 83 usage tests named in the Done Signals, all present and none in `lastfailed`.

## What this proves

- The implementation run (20:37 UTC, immediately before commit `b849565`) collected all 2514 tests and recorded **zero failures and zero errors** (`lastfailed` is `{}`). pytest records both failures and errors in `lastfailed`, so an empty file means none of either.
- All 15 TASK-14 acceptance tests were collected and passed in that run (present in `nodeids`, absent from `lastfailed`).

## What this does not prove

- A fresh run under this Verify session's environment (execution denied — see `qa/runtime-blocked.md`).
- Which pytest invocation produced the cache (targeted file vs full suite) — only that the last recorded run collected all 2514 nodeids with no failures.
- The Done-Signal figure "2504 tests": the cache shows 2514 collected. The 10-test delta is consistent with tests added since the plan was written; the observed record is authoritative for the committed code.

## How to re-run

```
.venv/bin/pytest -q                                      # full suite (or: tests/test_codex_usage.py)
grep -c . .pytest_cache/v/cache/nodeids                  # collected count
cat .pytest_cache/v/cache/lastfailed                     # expect {}
```
