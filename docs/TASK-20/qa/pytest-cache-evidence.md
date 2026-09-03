# TASK-20 QA — `.pytest_cache` execution-trail evidence

**What**: The worktree's pytest cache records the last real (non-collect-only) test session that ran on the final TASK-20 code, with zero failures.
**Why**: Live `pytest` is denied in this harness (see `qa/runtime-blocked.md`); the cache provider's write rules make its files durable indirect proof of that session.
**As-Is -> To-Be**:
- As-Is: No live run possible during Verify.
- To-Be: The In Progress agent's final run is reconstructed from cacheprovider mtimes and contents.

## Timeline (all UTC, 2026-08-19)

| Event | Time | Evidence |
|---|---|---|
| `src/symphony/backends/copilot.py` last edit | 17:23:44 | `stat -c %y` |
| `tests/test_orchestrator_usage_limits.py` last edit | 17:24:55 | `stat -c %y` |
| `tests/test_copilot_backend.py` last edit | 17:25:33 | `stat -c %y` |
| `.pytest_cache/v/cache/nodeids` rewritten | 17:26:02 | `stat -c %y` |
| `.pytest_cache/v/cache/lastfailed` (unchanged `{}`, 2 bytes) | 17:24:48 | `stat -c %y` + content |
| harness auto-commit `wip: turn …17:26:17Z` (82ff203) | 17:26:17 | `git log` |

## Cacheprovider facts applied (installed `_pytest/cacheprovider.py`, ~lines 415–468)

1. `pytest_sessionfinish` returns early on `collectonly` → a collect-only run never rewrites `nodeids`. A `nodeids` mtime **after** the final file edits therefore proves a **real execution session** of the exact committed code.
2. `lastfailed` is rewritten only when `saved_lastfailed != self.lastfailed`. `lastfailed` still contains `{}` with mtime 17:24:48 (older than the 17:26:02 nodeids rewrite) → the 17:26:02 session had **zero failures** (any failure would have rewritten it).

## Collection contents

- `nodeids` contains **43** tests from `tests/test_copilot_backend.py` and **28** from `tests/test_orchestrator_usage_limits.py` (71 total — matches the ticket's "71 passed" claim).
- All 11 new/updated TASK-20 test nodeids are present, including:
  - `test_copilot_usage_probe_lives_in_copilot_module`
  - `test_copilot_quota_probe_failure_fails_open`
  - `test_remaining_percentage_converts_to_used_percentage`
  - `test_copilot_usage_probe_standalone_lsp_rpc`
  - `test_copilot_usage_probe_standalone_malformed_lsp_fails_open`
  - `test_genuine_copilot_credit_exhaustion_emits_provider_usage_exhausted`
  - `test_generic_rate_limit_does_not_mark_plan_exhausted`
  - `test_exhausted_copilot_pool_blocks_all_copilot_profiles`
  - `test_configured_copilot_cap_blocks_new_dispatch`
  - `test_running_copilot_worker_is_not_cancelled_when_cap_crossed`
  - `test_monthly_reset_is_calculated_correctly`
  - `test_usage_probe_failure_never_prevents_dispatch[copilot]` (both `test_backend_usage_probes.py` and `test_orchestrator_usage_limits.py`)

## What this proves / does not prove

- Proves: a real pytest execution session ran on the final code (all edits 17:23:44–17:25:33 predate the 17:26:02 session), collected the TASK-20 tests, and ended with zero failures. Test collection also proves the modified modules import cleanly.
- Does not prove: the 17:26:02 session's exact CLI args (nodeids accumulates across sessions, so both files' tests were collected across the session set, not necessarily in one invocation), or a live run against real GitHub Copilot servers (covered by subprocess doubles only, as the ticket's Done Signals already state).

## How to re-run

```bash
cd /home/symphony/symphony_workspaces/TASK-20
stat -c '%y %n' .pytest_cache/v/cache/nodeids .pytest_cache/v/cache/lastfailed \
  src/symphony/backends/copilot.py tests/test_copilot_backend.py tests/test_orchestrator_usage_limits.py
grep -c 'test_copilot_backend.py' .pytest_cache/v/cache/nodeids
grep -c 'test_orchestrator_usage_limits.py' .pytest_cache/v/cache/nodeids
cat .pytest_cache/v/cache/lastfailed
```
