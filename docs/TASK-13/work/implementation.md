# Implementation Notes — TASK-13: Usage-Aware Agent Profiles Stage 3

## Summary of Changes

### 1. `src/symphony/orchestrator/usage.py`
- Implemented `ProviderUsageManager`:
  - Cache TTL support (default 60s) via `cache_ttl_s` and `_last_fetched`.
  - `snapshot(pool_id: str) -> ProviderUsageSnapshot | None` to retrieve cached telemetry.
  - `set_snapshot(pool_id: str, snapshot: ProviderUsageSnapshot)` for testing and notification updates.
  - `refresh(pool_id: str, source: str | None = None) -> ProviderUsageSnapshot | None` to probe provider quotas asynchronously. On probe failure, retains last known snapshot and marks `stale=True`.
  - `refresh_if_needed(pool_id: str, source: str, *, force: bool = False)` checks TTL and window reset timestamps.
  - `evaluate(pool_id: str, pool: UsagePoolConfig) -> UsageDecision`:
    - Returns `READY` if snapshot is `None`, `stale=True`, or `authoritative=False` (fail-open invariant).
    - Checks if `window.resets_at <= now`; expired windows fail open rather than blocking indefinitely.
    - Returns `WAIT_PROVIDER_USAGE` if `hard_limit_reached=True` (and not all resets passed) or if any configured window `used_percent >= cap`.
    - Returns `READY` when all windows are under cap.
  - `format_wait_reason(...)` constructs descriptive, actionable reason messages including cap details and ISO formatted reset timestamps.

### 2. `src/symphony/orchestrator/__init__.py`
- Re-exported `ProviderUsageManager`, `UsageDecision`, `READY`, `WAIT_PROVIDER_USAGE`, and `format_wait_reason`.

### 3. `src/symphony/orchestrator/core.py`
- Injected `ProviderUsageManager` into `Orchestrator.__init__` with fallback to a default instance.
- Exposed `usage_manager` property on `Orchestrator`.
- Added `_eligibility_usage_decision(issue, cfg)`:
  - Resolves profile & pool reference via `cfg.selection_for_state(issue.state, ...)`.
  - Looks up pool configuration in `cfg.usage_pools`.
  - Dispatches `self._usage_manager.evaluate(pool_id, pool)`.
  - Returns `_EligibilityDecision(_EligibilityDisposition.WAIT_NON_SLOT, "waiting_provider_usage", reason)` on usage cap / hard limit.
- Updated `_eligibility_decision` chain to order: `ownership -> contract -> usage -> contention`.
- In `_on_tick`: refreshes configured usage pools if needed via `_usage_manager.refresh_if_needed()`.

### 4. Tests
- Created `tests/test_orchestrator_usage_limits.py` with 27 tests covering Stage 6.10, 6.11, and 6.13 (scheduler eligibility, same-pool blocking, cross-provider independence, exact/below cap semantics, missing snapshot / probe exception / non-authoritative fail-open, reset safety, running worker non-cancellation, 8-kind fail-open invariant).
- Extended `tests/test_usage_limits.py` with 11 unit tests for `ProviderUsageManager` methods.
