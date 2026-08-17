# TASK-14 Work Details: Codex Usage Probe & Provider Capacity Handling

## Architecture & Integration Points

1. **`src/symphony/backends/__init__.py`**:
   - `EVENT_PROVIDER_USAGE_EXHAUSTED = "provider_usage_exhausted"` added to normalized event vocabulary.
   - `ProviderCapacityError(Exception)` with `pool_id: str`, `resets_at: datetime | None`, `message: str`.
   - `BackendInit` extended with `usage_manager: Any | None = None` and `usage_pool: str | None = None`.

2. **`src/symphony/backends/usage.py`**:
   - `get_usage_probe("codex")` returns `CodexUsageProbe`.

3. **`src/symphony/backends/codex.py`**:
   - `normalize_codex_rate_limits`: extracts windows mapping `300` -> `"five_hour"`, `10080` -> `"weekly"`, `<N>` -> `"<N>_minutes"`. Ignores position (`primary`/`secondary`).
   - `resetsAt` conversion parses Unix epoch (seconds/ms) or ISO timestamps to UTC `datetime`.
   - `hard_limit_reached` detected via `rateLimitReachedType` in `("hard", "overall", "quota", "usage")` or `raw.get("rateLimitReached") is True` or `raw.get("hard_limit_reached") is True`.
   - Distinguishes ChatGPT subscription vs API key: if auth mode is API key (`auth_mode in ("apiKey", "api_key")` or `authMode` / `accountType` is API key), snapshot sets `authoritative=False`, ensuring subscription caps never block API-key dispatch.
   - `CodexUsageProbe`: implements `fetch_usage() -> ProviderUsageSnapshot | None` by calling `account/rateLimits/read` (and `account/read` if available). Fails open on any error.
   - `CodexAppServerBackend`: updates `usage_manager` immediately on `account/rateLimits/updated` and emits `EVENT_NOTIFICATION`.
   - Emits `EVENT_PROVIDER_USAGE_EXHAUSTED` and raises `ProviderCapacityError` on genuine subscription/plan quota exhaustion, while standard RPM/429 errors follow normal retry handling.

4. **`src/symphony/orchestrator/core.py` & `entries.py`**:
   - `RunningEntry` tracks `hit_provider_usage_exhausted`, `provider_usage_exhausted_pool_id`, and `provider_usage_exhausted_resets_at`.
   - `_on_codex_event` handles `EVENT_PROVIDER_USAGE_EXHAUSTED` by updating the pool snapshot to hard-limited, marking the entry, and cancelling the worker task for fast return.
   - `_run_agent_attempt` catches `ProviderCapacityError`, updates the shared pool snapshot, and returns without raising an uncaught exception.
   - `_on_worker_exit_impl` handles provider exhaustion by un-claiming the issue, clearing retry attempts without scheduling a retry, allowing the scheduler to derive `waiting_provider_usage` on the next tick without consuming retries.
