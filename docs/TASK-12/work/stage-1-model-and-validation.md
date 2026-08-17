# Stage 1 Work Notes: Usage-Aware Agent Profiles Config & Normalized Usage Model

## Background & Objectives
Stage 1 of the Usage-Aware Agent Profiles plan introduces:
1. `UsagePoolConfig` value type:
   - `source: str`
   - `caps: dict[str, float]`
2. `ServiceConfig` extension:
   - `usage_pools: dict[str, UsagePoolConfig] = field(default_factory=dict)`
   - Backwards compatible with existing configurations.
3. `AgentProfileConfig` extension:
   - `usage_pool: str | None = None`
   - Backwards compatible (None = default to profile kind).
4. Validation rules in `src/symphony/workflow/builder.py`:
   - `usage_pools` must be a mapping.
   - `source` is required and non-empty string.
   - `caps` is a mapping of window name to numeric value `0 < value <= 100`.
   - Arbitrary window names (e.g. `five_hour`, `weekly`, `daily`, `monthly`, `rolling_7d`) allowed.
   - Unknown `usage_pool` reference in `agent_profiles` is rejected at load time with `ConfigValidationError`.
   - Field `usage_pool` is allowed for all backend kinds in `PROFILE_FIELDS_BY_KIND`.
5. Normalized quota types in `src/symphony/backends/usage.py`:
   - `UsageWindow(key, used_percent, remaining_percent, resets_at)`
   - `ProviderUsageSnapshot(pool_id, source, windows, hard_limit_reached, authoritative, observed_at, stale)`
   - `UsageProbe` protocol (`async def fetch_usage(self) -> ProviderUsageSnapshot | None`)
   - `USAGE_PROBES` registry and fail-open lookup (`get_usage_probe(source)` returning `None` for missing probes).
