# Diff review — TASK-13 Verify (2026-08-17)

Reviewed commit: d250c37 `wip: turn 2026-08-17T20:13:08Z` (the only commit on
`symphony/TASK-13` above develop tip cedcd2c).

Command: `git diff develop..HEAD` / `git show d250c37`.

## Scope: 8 files, +1458/-49

| File | Change | Ticket scope? |
|---|---|---|
| src/symphony/orchestrator/usage.py | +222 new: ProviderUsageManager | AC1-5 |
| src/symphony/orchestrator/__init__.py | +13 exports | Done Signals |
| src/symphony/orchestrator/core.py | +163/-49 | AC6-8 |
| tests/test_orchestrator_usage_limits.py | +750 new | AC9 (6.10/6.11) |
| tests/test_usage_limits.py | +263/-11 | AC9 (manager units) |
| docs/TASK-13/work/plan.md, implementation.md | +74 | evidence |
| docs/TASK-13/reproduce/eval.md | +22 | evidence |

No files outside ticket scope. The only test-file removals are an unused
`AgentProfileConfig` import (no remaining usage in the file).

## Functional change set in core.py (non-formatting)

- `usage.py:40` `ProviderUsageManager.__init__` takes `cache_ttl_s` (default
  60s, `usage.py:37`), injected probes/factory/clock.
- `core.py:754` constructor param `usage_manager`; `core.py:768` default
  instance; `core.py:929` `usage_manager` property.
- `core.py:3731-3743` tick refresh loop: `refresh_if_needed(pool_id,
  pool_cfg.source)` per configured pool, failures logged not raised.
- `core.py:5528-5560` `_eligibility_usage_decision` — resolves selection via
  `cfg.selection_for_state`, pool = `profile_cfg.usage_pool` or
  `selection.kind`, returns `WAIT_NON_SLOT`/`waiting_provider_usage` on
  `WAIT_PROVIDER_USAGE`, `None` otherwise (fail open, incl.
  `ConfigValidationError`).
- `core.py:5562-5580` `_eligibility_decision` chain order:
  ownership -> contract -> usage -> contention.
- `_latest_rate_limits` (`core.py:819`, set at `core.py:8900`, exposed at
  `core.py:2879`) remains as telemetry-only state; no scheduling path reads
  it. Scheduling is now usage-pool-aware.

## Formatting-only hunks (non-blocking observation)

Roughly 40 hunks of core.py are pure re-wrapping to ~88 columns in code paths
unrelated to usage (e.g. `_run_record_payload`, `_worker_loop` stage-profile
reporting). Behavior-identical, same file the ticket touches, but strictly
they are drive-by reformat noise. Severity: LOW (cosmetic), noted for the
record — not a merge blocker.

## Anchor map for the AC scorecard

| AC | Primary anchors |
|---|---|
| 1 manager with snapshot(refresh)/snapshot()/evaluate, ~60s TTL | `usage.py:37,40,58,90,150` |
| 2 fail-open on None/stale/non-authoritative | `usage.py:154-161` |
| 3 WAIT on hard_limit / window >= cap | `usage.py:166-193` |
| 4 probe caches, failure retains+stales, no telemetry fail open | `usage.py:90-126` |
| 5 reset passed + refresh fails -> fail open | `usage.py:128-148,176-183,187-192` |
| 6 core gains `_eligibility_usage_decision`, chain order | `core.py:5528,5574-5578` |
| 7 derived WAIT_NON_SLOT `waiting_provider_usage`, auto-clear | `core.py:5555-5558`, scan re-evaluates per tick (`core.py:3934`) |
| 8 caps never cancel running worker | dispatch scan short-circuits `running` before eligibility (`core.py:3893-3903`) |
| 9 tests 6.10+6.11 green | `tests/test_orchestrator_usage_limits.py`, `tests/test_usage_limits.py` |
