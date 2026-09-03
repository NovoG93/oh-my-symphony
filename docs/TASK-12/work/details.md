# TASK-12 Stage 1 Work Details: Usage-Aware Agent Profiles Config & Normalized Usage Types

## Overview
Implemented Stage 1 of the Usage-Aware Agent Profiles architecture:
- `UsagePoolConfig` dataclass and `usage_pools` mapping in `ServiceConfig`.
- `usage_pool` reference in `AgentProfileConfig`.
- `PROFILE_FIELDS_BY_KIND` allowlist update to include `usage_pool` for all agent kinds.
- Strict validation in `src/symphony/workflow/builder.py`:
  - `usage_pools` mapping validation.
  - `source` string requirement and `caps` percentage range validation `(0 < v <= 100)`.
  - Rejection of unknown `usage_pool` references in `agent_profiles`.
- Normalized provider-usage types and probe protocol in `src/symphony/backends/usage.py`:
  - `UsageWindow`
  - `ProviderUsageSnapshot`
  - `UsageProbe` protocol
  - `USAGE_PROBES` registry and `get_usage_probe` with fail-open semantics.

## Test Results
- Suite: 30 unit tests in `tests/test_usage_limits.py` + 16 unit tests in `tests/test_workflow_agent_profiles.py`.
- Full regression suite: 2451 passed, 9 skipped in 169s.
- Type checks: `symphony-pyright` 0 errors, 0 warnings.
