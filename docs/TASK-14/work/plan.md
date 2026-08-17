# TASK-14: Codex Usage Probe & Provider-Exhaustion Classification — Plan

**What**: Implement authoritative Codex App Server rate-limit probe and runtime provider-exhaustion handling.
**Why**: Ensures Codex profiles share accurate quota snapshots and provider capacity exhaustion waits for reset without burning retry attempts.
**As-Is -> To-Be**:
- As-Is: Codex backend receives `account/rateLimits/updated` passively without updating shared `ProviderUsageManager` cache; no `CodexUsageProbe` or duration normalization; runtime quota exhaustion is classified as a generic failure that burns retries.
- To-Be: `CodexUsageProbe` polls `account/rateLimits/read`; windows normalized by duration (`five_hour`, `weekly`, `<N>_minutes`); notifications update shared cache immediately; API key auth bypasses subscription caps; `EVENT_PROVIDER_USAGE_EXHAUSTED` and `ProviderCapacityError` classify quota exhaustion as `waiting_provider_usage` without consuming retry budgets.

## Concrete Steps

1. **Backend Event & Error Types (`src/symphony/backends/__init__.py`)**:
   - Define `EVENT_PROVIDER_USAGE_EXHAUSTED = "provider_usage_exhausted"`.
   - Define `ProviderCapacityError` dataclass / Exception with `pool_id`, `resets_at`, and message.
   - Update `BackendInit` to accept optional `usage_manager` and `usage_pool`.

2. **Usage Probe Registry (`src/symphony/backends/usage.py`)**:
   - Register `CodexUsageProbe` in `USAGE_PROBES` with fail-open resolution.

3. **Codex Normalization & Probe (`src/symphony/backends/codex.py`)**:
   - Implement `normalize_codex_rate_limits(raw, *, pool_id="codex", auth_mode=None)` normalizing windows by `windowDurationMins` (300 -> `five_hour`, 10080 -> `weekly`, other -> `<N>_minutes`), computing `used_percent` and `remaining_percent`, parsing `resetsAt`, extracting `hard_limit_reached`, and setting `authoritative=False` for API-key auth.
   - Implement `CodexUsageProbe(UsageProbe)` calling `account/rateLimits/read` via client/backend or standalone process.
   - Wire `CodexAppServerBackend` to update `usage_manager` immediately on `account/rateLimits/updated` notifications.
   - Detect genuine quota exhaustion in `_raise_for_terminal_status` vs generic 429/RPM, emitting `EVENT_PROVIDER_USAGE_EXHAUSTED` and raising `ProviderCapacityError`.

4. **Orchestrator Integration (`src/symphony/orchestrator/core.py` & `entries.py`)**:
   - In `_on_codex_event`, handle `EVENT_PROVIDER_USAGE_EXHAUSTED` by updating the shared usage snapshot to `hard_limit_reached=True`, flagging `hit_provider_usage_exhausted`, and cleanly cancelling the worker attempt.
   - In `_on_codex_event`, update `usage_manager` on incoming rate-limit payloads.
   - In `_run_agent_attempt`, pass `usage_manager` to `BackendInit` and catch `ProviderCapacityError`, terminating the attempt without error escalation.
   - In `_on_worker_exit_impl`, handle `provider_usage_exhausted` by clearing retry attempts without scheduling a retry, leaving the ticket ready for derived `waiting_provider_usage` eligibility on the next scheduler tick.

5. **Test Suite**:
   - Add Stage 6.3 tests covering duration normalization, position independence, multiple limit IDs, updated notification, unknown window handling, hard limit normalization, API key auth non-blocking, and probe execution.
   - Add Stage 6.11 tests covering provider exhaustion retry preservation and genuine exhaustion vs RPM classification.
