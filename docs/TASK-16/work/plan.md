# TASK-16: Stage 5 Usage-Aware Agent Profiles — Work Notes

## Context & Objectives
Stage 5 exposes usage pools and runtime provider quota metrics to operators via the REST API and the web UI.

### Key Surfaces
1. `src/symphony/orchestrator/core.py`:
   - Add `_provider_usage_projection()` method to `Orchestrator` projecting per-pool `ProviderUsageSnapshot` info (`source`, `windows` with `used_percent`, `remaining_percent`, `resets_at`, `status`, `stale`, `authoritative`).
   - Augment `snapshot()` to include `"provider_usage": self._provider_usage_projection()`.
   - Ensure `remaining_percent` defaults to `100 - used_percent` when not explicitly set.

2. `src/symphony/webapi.py`:
   - Extend `_workflow_payload(cfg)` to include configured `usage_pools` (`source` + `caps`).
   - Extend `_PUBLIC_SCHEDULE_REASONS` with `"waiting_provider_usage": "waiting for provider capacity"`.
   - In `handle_board`, include `provider_usage` in the board payload.

3. `src/symphony/web/static/app.js`:
   - Add `waiting_provider_usage: t('schedule.reasonProviderUsage')` to `scheduleReasonLabel()`.
   - Add `buildProviderUsageCard(usagePools, providerUsage)` near `buildAgentPolicyCard` in workflow editor.
   - Render usage progress bars, configured caps, remaining percentages, reset times, status badges (`Available`, `Capacity paused`, `Usage unavailable`), stale and estimated badges.
   - Handle empty/unknown/stale/estimated gracefully.

4. `src/symphony/web/static/i18n.js`:
   - Add English and Korean localized strings for Provider Usage, Usage pool, 5-hour, Weekly, Daily, Monthly, Remaining, Configured cap, Usage unavailable, stale, Waiting for provider capacity, Resets at, Available after, Estimated, Authoritative, Capacity paused.

5. `src/symphony/web/static/style.css`:
   - Add CSS classes for provider usage card, pool sections, progress bars (including paused and estimated modifiers), and status chips.

6. Contract Tests (Stage 6.12):
   - `tests/test_webapi.py`
   - `tests/test_web_static_contract.py`
   - `tests/test_i18n.py`
