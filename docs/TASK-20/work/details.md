# TASK-20 Work Notes & Implementation Details

**What**: Implement Copilot Phase 3 authoritative quota probing and runtime capacity exhaustion.
**Why**: Prevent running tasks when Copilot quota/credits are exhausted; integrate with Symphony usage-aware scheduling.
**As-Is -> To-Be**:
- As-Is: Copilot probe was a placeholder returning cached snapshot/None; exhaustion parsing handled basic strings.
- To-Be: CopilotUsageProbe queries CLI internal JSON-RPC (`account.getQuota`) over LSP framing, normalizes `premium_interactions` to `monthly` window, and fails open; runtime exhaustion triggers `EVENT_PROVIDER_USAGE_EXHAUSTED` -> `ProviderCapacityError`.

## Key Components

1. `_is_genuine_copilot_exhaustion(text: str) -> bool`:
   - Filters out RPM/TPM transients and generic 429 errors.
   - Detects genuine quota/credit exhaustion keywords.

2. `normalize_copilot_quota(raw: dict, pool_id: str) -> ProviderUsageSnapshot`:
   - Extracts `premium_interactions` bucket.
   - Converts `remainingPercentage` to `used_percent = 100.0 - remainingPercentage`.
   - Parses `resetDate` into `resets_at` (UTC datetime); falls back to `next_month_first_day_utc()`.
   - Flags `hard_limit_reached` when `hasQuota` is false or remaining percentage is <= 0.

3. `CopilotUsageProbe(UsageProbe)`:
   - Spawns `copilot --server --stdio --no-auto-update --log-level error`.
   - Sends LSP-framed JSON-RPC request for `account.getQuota`.
   - Reads LSP frames using `_read_lsp_message`.
   - Returns normalized `ProviderUsageSnapshot` with `authoritative=True`.
   - Fails open (`None`) on timeouts, disconnects, or malformed data.
