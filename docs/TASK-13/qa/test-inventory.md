# Test inventory — TASK-13 Verify (2026-08-17)

Command: `grep -c "def test" tests/test_orchestrator_usage_limits.py
tests/test_usage_limits.py` and `grep` on `.pytest_cache/v/cache/nodeids`.

## Counts

| File | Test functions | Parametrized ids | Total collected |
|---|---|---|---|
| tests/test_orchestrator_usage_limits.py | 16 | 5 (`test_any_configured_window_can_block`) + 8 (`test_usage_probe_failure_never_prevents_dispatch`) | 27 |
| tests/test_usage_limits.py | 31 (20 Phase-1 + 11 new manager) | 10 (Phase-1 parametrizations) | 41 |
| Total | | | 68 |

68 matches the "Expected: 68 passed" figure recorded in
`docs/TASK-13/reproduce/eval.md`.

## Mapping to the ticket's AC list

- same-pool blocked: `test_all_profiles_of_same_pool_are_blocked_by_cap`
- other provider unaffected: `test_other_provider_remains_schedulable`
- at/below cap semantics: `test_usage_exactly_at_cap_blocks_dispatch`,
  `test_usage_below_cap_allows_dispatch`, `test_any_configured_window_can_block`
- missing snapshot / probe exception / non-authoritative / no-policy fail
  open: `test_missing_usage_snapshot_fails_open`,
  `test_probe_exception_fails_open`, `test_non_authoritative_usage_fails_open`,
  `test_no_policy_does_not_block_dispatch`
- reset behavior: `test_task_becomes_ready_after_usage_reset`,
  `test_failed_refresh_after_reset_fails_open`
- stale safety: `test_stale_snapshot_fails_open`
- running-worker not cancelled: `test_configured_cap_does_not_cancel_running_worker`
- hard limit: `test_hard_limit_reached_blocks_dispatch`
- shared explicit pool: `test_wrapper_backend_explicit_usage_pool_shares_quota`
- global fail-open ×8 kinds: `test_usage_probe_failure_never_prevents_dispatch`
- manager units: `test_provider_usage_manager_*` ×11 in test_usage_limits.py
  (None/stale/non-authoritative -> READY, hard limit -> WAIT, window over/under
  cap, resets_at passed -> READY, refresh success caches, refresh failure
  marks stale, TTL respected, format_wait_reason).

## What this proves / does not prove

Proves: all AC-mapped tests are committed and collectible (nodeids).
Does not prove: their runtime results in a fresh session — see
`qa/pytest-cache-evidence.md` (indirect) and `qa/runtime-blocked.md`
(fresh run denied).

## Re-run

`.venv/bin/pytest tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py -q`
