# TASK-16: Stage 5 Implementation Report

## Summary of Changes
1. `src/symphony/orchestrator/core.py`:
   - Added `_provider_usage_projection()` helper projecting runtime data across all configured and active usage pools.
   - Augmented `snapshot()` projection to expose `provider_usage` mapping with per-pool `source`, `windows` (`used_percent`, `remaining_percent`, `resets_at`), `status` (`available` | `capacity_paused` | `unavailable`), `stale`, and `authoritative`.
   - Guaranteed automatic calculation `remaining_percent = 100 - used_percent` when not explicitly supplied.

2. `src/symphony/webapi.py`:
   - Updated `_workflow_payload()` to serialize configured `usage_pools` (`source` and `caps`).
   - Extended `_PUBLIC_SCHEDULE_REASONS` with `"waiting_provider_usage": "waiting for provider capacity"`.
   - Updated `handle_board` to include `provider_usage` in the board API payload.

3. `src/symphony/web/static/app.js`:
   - Extended `api` object with `getState: () => apiRequest('/state')`.
   - Added `waiting_provider_usage: t('schedule.reasonProviderUsage')` to `scheduleReasonLabel()`.
   - Implemented `buildProviderUsageCard(usagePools, providerUsage)` rendering:
     - Header with pool name, pool source, status badge (`Available`, `Capacity paused`, `Usage unavailable`), stale badge, and authoritative/estimated badge.
     - Blocked state notice showing paused tasks and waiting for provider capacity.
     - Progress bar with percentage fill and modifier classes for paused (`.usage-bar-fill--paused`) and estimated (`.usage-bar-fill--estimated`) states.
     - Meta rows with used %, remaining %, configured cap %, and reset timestamp / available after formatting.
   - Mounted `buildProviderUsageCard` in `buildWorkflowEditor` near `buildAgentPolicyCard` and on the Settings page.

4. `src/symphony/web/static/i18n.js`:
   - Added English and Korean localizations for all usage labels and `schedule.reasonProviderUsage`.

5. `src/symphony/web/static/style.css`:
   - Added lightweight CSS rules for `.provider-usage-card`, `.provider-usage-pool`, `.usage-bar-track`, `.usage-bar-fill`, `.usage-bar-fill--paused`, `.usage-bar-fill--estimated`, `.chip-estimated`, `.chip-stale`.

6. Tests:
   - Added Stage 6.12 tests to `tests/test_webapi.py` and `tests/test_web_static_contract.py`.
   - Verified 100% pass across all 2548 tests and `scripts/check_i18n.py`.
