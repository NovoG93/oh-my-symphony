# TASK-17 Stage 6 Implementation Details

## Overview
Stage 6 delivers the comprehensive test suite across all 13 sub-areas (Stages 6.1-6.13) and finalizes documentation for the Usage-Aware Agent Profiles feature.

## Test Inventory & Coverage Breakdown

### 1. Stage 6.1 Configuration Tests (`tests/test_usage_limits.py`, `tests/test_workflow_agent_profiles.py`)
- `test_usage_pool_config_dataclass_fields`: Frozen dataclass validation for `UsagePoolConfig`.
- `test_usage_limit_is_shared_by_profiles_of_same_kind`: Shared `usage_pools` loaded without explicit profile references.
- `test_pi_profile_can_explicitly_share_codex_pool`: Wrapper binding for `pi`.
- `test_opencode_and_prime_agent_profiles_can_explicitly_bind_usage_pool`: Wrapper binding for `opencode` and `prime-agent`.
- `test_usage_cap_rejects_invalid_percent`: Parameterized over negative, zero, >100, string, boolean, and null values.
- `test_unknown_usage_pool_reference_is_rejected`: Rejects unknown pool name with `ConfigValidationError`.
- `test_missing_usage_pools_is_backward_compatible`: Configs without `usage_pools` load with empty dict default.
- `test_partial_usage_policy_is_valid`: Partial pool configurations with single windows load properly.
- `test_generic_daily_window_is_valid` & `test_generic_monthly_window_is_valid`: Non-5h/weekly window support.
- `test_arbitrary_window_names_are_supported`: Arbitrary window keys accepted and mapped to floats.
- `test_usage_pools_validation_rejects_*`: Rejection of non-mappings, empty names, non-string sources, unsupported fields.

### 2. Stage 6.2 Generic Usage-Pool Tests (`tests/test_usage_limits.py`, `tests/test_orchestrator_usage_limits.py`)
- `test_profiles_with_same_usage_pool_share_limit`: Shared pool exceeding cap blocks all consumers.
- `test_pi_copilot_is_not_blocked_by_codex_limit`: Distinct pools do not block each other.
- `test_any_configured_window_can_block`: Parameterized test across `five_hour`, `weekly`, `daily`, `monthly`, `custom_window`.
- `test_estimated_usage_never_blocks_scheduler`: `authoritative=False` snapshots return `READY`.

### 3. Stage 6.3 Codex Probe Tests (`tests/test_codex_usage.py`)
- `test_codex_normalizes_five_hour_window`: 300 minutes -> `five_hour`.
- `test_codex_detects_windows_by_duration_not_position`: Independent of primary/secondary keys.
- `test_codex_rate_limits_read_normalization`: Payload from `account/rateLimits/read`.
- `test_codex_multiple_limit_ids_are_preserved`: Arbitrary duration normalization (`<N>_minutes`).
- `test_codex_updated_notification_updates_shared_pool`: `account/rateLimits/updated` immediately sets snapshot.
- `test_codex_unknown_window_is_preserved_or_ignored_safely`: Safe fallback for unknown keys.
- `test_codex_hard_limit_reached_is_normalized`: `rateLimitReachedType` hard-limit flag parsing.
- `test_codex_api_key_auth_does_not_apply_chatgpt_cap`: `authoritative=False` under API key auth.
- `test_codex_usage_probe_calls_rate_limits_read`: Probe invokes RPC method.
- `test_codex_usage_probe_fails_open_on_error`: Probe exception returns `None`.
- `test_genuine_provider_exhaustion_detection`: Genuine quota limit vs transient RPM/TPM 429 errors.
- `test_provider_exhaustion_does_not_consume_retry_budget`: Worker exit on capacity exhaustion preserves retry count.

### 4. Stage 6.4-6.9 Backend Probes Tests (`tests/test_backend_usage_probes.py`)
- **AGY (6.4)**: `test_agy_quota_probe_uses_read_only_command`, `test_agy_structured_quota_is_normalized`, `test_agy_model_specific_quota_buckets_are_preserved`, `test_agy_probe_fails_open_on_error`.
- **Claude (6.5)**: `test_claude_normalizes_subscription_limits`, `test_claude_missing_rate_limits_returns_unknown`, `test_claude_missing_single_window_is_supported`, `test_claude_unknown_quota_fails_open`, `test_claude_limit_error_sets_hard_limit`, `test_claude_genuine_exhaustion_detection`.
- **Gemini (6.6)**: `test_gemini_missing_programmatic_quota_fails_open`, `test_gemini_quota_exhaustion_is_not_normal_retry`, `test_gemini_reset_time_is_extracted_when_available`, `test_gemini_usage_snapshot_normalization`.
- **Kiro (6.7)**: `test_kiro_missing_usage_probe_fails_open`, `test_kiro_credit_exhaustion_blocks_new_dispatch`, `test_kiro_monthly_credit_window_can_be_normalized`.
- **OpenCode (6.8)**: `test_opencode_local_stats_are_non_authoritative`, `test_opencode_bound_to_codex_uses_codex_pool`, `test_opencode_go_estimate_does_not_block_scheduler`, `test_opencode_exhaustion_detection`.
- **Pi & Prime Agent (6.9)**: `test_pi_requires_bound_pool_for_subscription_policy`, `test_pi_profile_can_share_codex_usage_pool`, `test_prime_agent_uses_same_usage_pool_resolution_as_pi`, `test_prime_claude_does_not_implicitly_use_claude_code_pool`, `test_github_copilot_usage_probe_fails_open`, `test_pi_exhaustion_detection`.

### 5. Stage 6.10 Scheduler & 6.11 Worker Semantics (`tests/test_orchestrator_usage_limits.py`)
- `test_all_profiles_of_same_pool_are_blocked_by_cap`: All issues under the same pool receive `waiting_provider_usage`.
- `test_other_provider_remains_schedulable`: Other unaffected provider pools remain `ready`.
- `test_usage_exactly_at_cap_blocks_dispatch`: Boundary check at cap value.
- `test_usage_below_cap_allows_dispatch`: Boundary check below cap value.
- `test_task_becomes_ready_after_usage_reset`: Dynamic clear of wait state upon reset.
- `test_failed_refresh_after_reset_fails_open`: Expired reset timestamp with failed probe fails open.
- `test_configured_cap_does_not_cancel_running_worker`: Cap crossing does not interrupt in-flight worker tasks.

### 6. Stage 6.12 API/UI Contract (`tests/test_webapi.py`, `tests/test_web_static_contract.py`, `tests/test_i18n.py`)
- `test_workflow_api_exposes_usage_pools`: API serializes `{pool: {source, caps}}`.
- `test_snapshot_exposes_provider_usage`: Snapshot contains `provider_usage` with source, windows, status, stale, authoritative.
- `test_remaining_percent_is_100_minus_used_percent`: Defaults remaining to `100 - used_percent`.
- `test_provider_usage_card_exists`: Card HTML/DOM, progress bars, and CSS classes exist.
- `test_waiting_provider_usage_has_translation`: Localization in English and Korean.
- `test_usage_unknown_is_rendered_without_error`: Fallback rendering for unknown usage.
- `test_estimated_usage_is_visually_distinguished`: Estimated modifier class for non-authoritative snapshots.

### 7. Stage 6.13 Global Fail-Open Invariant
- `test_usage_probe_failure_never_prevents_dispatch`: Parameterized across all 8 backend kinds (`codex`, `claude`, `agy`, `gemini`, `kiro`, `opencode`, `pi`, `prime-agent`), verifying that when any probe raises an exception or telemetry is missing/stale, scheduling decisions unconditionally evaluate to `READY`.

## Documentation Deliverables
- `README.md`: Added `#### Usage Pools & Quota Management (usage_pools)` detailing the profile-vs-pool boundary, shared quotas, fail-open invariants, capacity exhaustion, and UI cards.
- `WORKFLOW.example.md` & `WORKFLOW.file.example.md`: Documented `usage_pools:` example blocks and `usage_pool:` references in `agent_profiles:`.
- `docs/features/agent-profiles.md`: Documented Stage 6 comprehensive test suite and global fail-open invariant.
- `docs/llm-wiki/usage-aware-agent-profiles.md`: Updated with Stage 6 summary and decision log.
