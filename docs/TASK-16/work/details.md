# TASK-16: Stage 5 Work Details

## Projection Specification
- `provider_usage` in `core.py`:
  - Iterates over all known pools in `cfg.usage_pools` and `_usage_manager.snapshots`.
  - For each pool, maps `source`, `windows`, `status` (`available` | `capacity_paused` | `unavailable`), `stale`, and `authoritative`.
  - Computes `remaining_percent = 100 - used_percent` when not provided.
  - Converts `resets_at` datetime instances to ISO 8601 strings.

## Web API Integration
- `_workflow_payload` in `webapi.py`:
  - Serializes `cfg.usage_pools` as `{ pool_name: { "source": pool.source, "caps": pool.caps } }`.
- `_PUBLIC_SCHEDULE_REASONS`:
  - Added `"waiting_provider_usage": "waiting for provider capacity"`.
- `handle_board`:
  - Exposes `provider_usage` in the board JSON response.

## Web UI Card Implementation
- `app.js`:
  - `buildProviderUsageCard(usagePools, providerUsage)`:
    - Creates `.card-panel.provider-usage-card`.
    - Handles pool items with header, status badge, stale chip, and estimated chip.
    - Renders progress bar `.usage-bar-fill` with `.usage-bar-fill--paused` and `.usage-bar-fill--estimated`.
    - Renders window meta (used %, remaining %, configured cap, reset/available-after timestamp).
  - Added `waiting_provider_usage: t('schedule.reasonProviderUsage')` in `scheduleReasonLabel`.
- `i18n.js`:
  - Added full translation tables in both English and Korean.
- `style.css`:
  - Lightweight rules for `.provider-usage-card`, `.usage-bar-track`, `.usage-bar-fill`, `.usage-window-row`, `.chip-estimated`, `.chip-stale`.
