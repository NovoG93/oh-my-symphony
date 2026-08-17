# Usage-aware agent profiles — Stage 1 usage-pool model

**Summary:** TASK-12 delivered Stage 1 of the Usage-Aware Agent Profiles
feature: a shared usage-pool configuration model, load-time validation, and
provider-independent normalized quota types. Usage is modeled per shared
pool/provider quota (`usage_pools:`), never per named profile — a profile
only references a pool; it never carries cap values.

**Model & validation invariants:**
- `UsagePoolConfig` (`src/symphony/workflow/config.py`): frozen dataclass
  with `source: str` and `caps: dict[str, float]` (per-window percentage
  caps).
- `ServiceConfig.usage_pools: dict[str, UsagePoolConfig]` with
  `field(default_factory=dict)` — configs without `usage_pools:` load
  unchanged (backward compatible).
- `AgentProfileConfig.usage_pool: str | None = None` — `None` means default
  to `profile.kind`; explicit binding matters for multiplexing backends
  (opencode/pi/prime-agent) that can share another provider's pool.
- `PROFILE_FIELDS_BY_KIND` allowlists `usage_pool` for all 8 backend kinds.
- `_validated_usage_pools` (`src/symphony/workflow/builder.py`) enforces:
  `usage_pools` must be a mapping; pool names non-empty and unique after
  strip; pool entries a mapping with only `source`/`caps` keys; `source`
  required non-empty string; `caps` required mapping of window name ->
  number with `0 < v <= 100` (bool explicitly rejected — bool is an int
  subclass; NaN/inf also fail the float compare). Window names are
  arbitrary (`five_hour`/`weekly`/`daily`/`monthly`/`rolling_7d` all
  accepted).
- Unknown `usage_pool` references in `agent_profiles` raise
  `ConfigValidationError` at load — the check is always active because the
  sole caller of `_validated_agent_profiles` passes the validated
  `usage_pools` map.
- `src/symphony/backends/usage.py`: `UsageWindow`, `ProviderUsageSnapshot`
  (`authoritative: bool = True` — only authoritative telemetry may block
  scheduling), `UsageProbe` runtime-checkable protocol, `USAGE_PROBES`
  registry + `get_usage_probe()` returning `None` for missing/unsupported
  probes (fail open). No probe performs network calls yet.

**Stage boundary:** no runtime consumers — pool resolution, probe
invocation, and quota enforcement are Stages 2/3. Config accepts and
validates; nothing reads the pool at dispatch yet. Existing
`agent.kind` / `stage_kinds` / `agent_profiles` configs are unaffected.

**Evidence:** 46 acceptance tests — `tests/test_usage_limits.py` 30
(20 defs incl. an 11-case invalid-percent parametrize), 16 in
`tests/test_workflow_agent_profiles.py`; TASK-12 QA artefacts under
`docs/TASK-12/qa/`.

**Decision log:**
- 2026-08-17 | TASK-12 | Usage modeled per shared pool, not per profile:
  `UsagePoolConfig` + `usage_pools` map; `usage_pool` is a reference only.
  Stricter-than-spec: unsupported pool-entry fields rejected at load
  (matches the `agent_profiles` allowlist pattern).

**Last updated:** 2026-08-17 by TASK-12 Document.
