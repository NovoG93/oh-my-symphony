# TASK-12 Verify: Static Validation & Code Review (per AC)

Reviewed 2026-08-17 (Verify stage) against `git diff develop..HEAD` (full diff: `qa/diff.md`).
Note: the authoritative spec `/home/symphony/usage-aware-agent-profiles-plan.md` is outside this
session's permitted read directories (Read blocked), so conformance is anchored on the ticket ACs,
`## Plan`, and `docs/TASK-12/work/stage-1-model-and-validation.md` (which quotes Stage 1).

## AC1 — UsagePoolConfig frozen dataclass
- `src/symphony/workflow/config.py:123-128`: `@dataclass(frozen=True) class UsagePoolConfig` with
  `source: str`, `caps: dict[str, float]`. Matches spec exactly.
- Exported from `symphony.workflow` (`src/symphony/workflow/__init__.py` import + `__all__`).

## AC2 — ServiceConfig.usage_pools
- `src/symphony/workflow/config.py:775-776`: `usage_pools: dict[str, UsagePoolConfig] =
  field(default_factory=dict)`, appended after `agent_profiles`. New field is last with a default,
  so existing positional `ServiceConfig(...)` constructions remain source-compatible.

## AC3 — AgentProfileConfig.usage_pool
- `src/symphony/workflow/config.py:120`: `usage_pool: str | None = None` appended last (default
  None = default to profile.kind; runtime resolution is Stages 2/3, out of scope).
- `src/symphony/workflow/constants.py:103-163`: `"usage_pool"` added to `PROFILE_FIELDS_BY_KIND`
  for all 8 backend kinds (codex, claude, gemini, agy, kiro, opencode, pi, prime-agent).

## AC4 — builder.py usage_pools validation
- `src/symphony/workflow/builder.py:998-1081` `_validated_usage_pools`:
  - `None` -> `{}` (backward compatible); non-dict -> `ConfigValidationError("usage_pools must be a mapping")`.
  - Pool name: non-empty string after strip, duplicates rejected post-strip.
  - Pool entry must be a mapping; unsupported pool fields rejected (stricter than spec, but
    consistent with the codebase's agent_profiles allowlist pattern).
  - `source` required, non-empty string.
  - `caps` required mapping; window names arbitrary non-empty strings (five_hour/weekly/daily/
    monthly/rolling_7d all accepted); values: `bool` explicitly excluded (bool is an int subclass),
    must be int/float with `0.0 < v <= 100.0` — the float comparison also rejects NaN/inf.

## AC5 — unknown usage_pool reference rejected at load
- `src/symphony/workflow/builder.py:1203-1218`: `usage_pool` must be a non-empty string; if it is
  not in the validated `usage_pools` mapping, `ConfigValidationError("... references unknown usage
  pool ...")` is raised. Sole caller passes `usage_pools` (`builder.py:302-304`), so the check is
  always active during `build_service_config` (load).

## AC6 — normalized usage types + fail-open probe registry
- `src/symphony/backends/usage.py:1-46`: `UsageWindow` (key, used_percent, remaining_percent,
  resets_at), `ProviderUsageSnapshot` (pool_id, source, windows, hard_limit_reached,
  `authoritative: bool = True`, observed_at, stale), `@runtime_checkable class UsageProbe(Protocol)`
  with `async def fetch_usage(self) -> ProviderUsageSnapshot | None`, `USAGE_PROBES` registry, and
  `get_usage_probe(source)` returning `USAGE_PROBES.get(source)` -> `None` for missing probes
  (fail open). No probe performs network calls yet (Stage 2/3).

## Backward compatibility check
- Configs without `usage_pools` -> `{}`; profiles without `usage_pool` -> `None`.
- `_validated_agent_profiles` gained a keyword-only param with default; it has exactly one caller.
- No serialization/iteration over `AgentProfileConfig` fields anywhere in src (grep for
  `asdict`/`__dict__` over profile-related code: no matches), so the new field cannot leak into
  run records or TUI output unexpectedly.

## Noted, non-blocking
- `UsagePoolConfig.caps` is a mutable dict inside a frozen dataclass — per-spec type
  (`caps: dict[str, float]`), accepted tradeoff.
- No CRITICAL/HIGH/MEDIUM findings.
