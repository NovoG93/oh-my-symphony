# Work Notes — TASK-13: Usage-Aware Agent Profiles Stage 3

## Overview
Implement Stage 3 of the Usage-Aware Agent Profiles plan:
- Shared `ProviderUsageManager` in `src/symphony/orchestrator/usage.py`
- Wire `ProviderUsageManager` into scheduler eligibility in `src/symphony/orchestrator/core.py`
- Chain order: `ownership -> contract -> usage -> contention`
- Fail-open semantics, cache TTL (~60s), reset handling, non-cancellation of running workers, automatic re-dispatch when capacity returns.

## Architecture

### 1. `src/symphony/orchestrator/usage.py`
- `UsageDecision(str, Enum)`: `READY = "ready"`, `WAIT_PROVIDER_USAGE = "waiting_provider_usage"`
- `ProviderUsageManager`:
  - `snapshots: dict[str, ProviderUsageSnapshot]`
  - `cache_ttl_s: float = 60.0`
  - `snapshot(pool_id: str) -> ProviderUsageSnapshot | None`
  - `set_snapshot(pool_id: str, snapshot: ProviderUsageSnapshot) -> None`
  - `async def refresh(pool_id: str, source: str | None = None) -> ProviderUsageSnapshot | None`
  - `async def refresh_if_needed(pool_id: str, source: str, *, force: bool = False) -> ProviderUsageSnapshot | None`
  - `def evaluate(pool_id: str, pool: UsagePoolConfig) -> UsageDecision`
  - `def format_wait_reason(pool_id: str, pool: UsagePoolConfig, snapshot: ProviderUsageSnapshot | None) -> str`

### 2. `src/symphony/orchestrator/core.py`
- Add `self._usage_manager = ProviderUsageManager(...)`
- Add `_eligibility_usage_decision(issue, cfg)`:
  - Resolves profile & usage pool via `cfg.selection_for_state`
  - Defaults to `profile_cfg.usage_pool` or `selection.kind`
  - Evaluates against `cfg.usage_pools[pool_id]`
  - Returns `_EligibilityDecision(_EligibilityDisposition.WAIT_NON_SLOT, "waiting_provider_usage", reason)` if cap reached or hard limit hit
- Update `_eligibility_decision` chain:
  `ownership -> contract -> usage -> contention`
- Tick loop refreshes usage pools if needed via `_usage_manager.refresh_if_needed`
- Running workers are never cancelled by configured caps
- Quota wait is a derived scheduler state that clears automatically on later ticks

### 3. Tests
- `tests/test_orchestrator_usage_limits.py` (covering Stage 6.10, 6.11, 6.13)
- `tests/test_usage_limits.py` (unit tests for `ProviderUsageManager` evaluation, cache TTL, reset handling, and stale safety)
