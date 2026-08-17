# Usage-aware agent profiles — Stage 1 usage-pool model + Stage 3 enforcement

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

**Stage boundary (as of Stage 1):** no runtime consumers — pool resolution,
probe invocation, and quota enforcement are Stages 2/3. Config accepts and
validates; nothing reads the pool at dispatch yet. Existing
`agent.kind` / `stage_kinds` / `agent_profiles` configs are unaffected.

**Evidence:** 46 acceptance tests — `tests/test_usage_limits.py` 30
(20 defs incl. an 11-case invalid-percent parametrize), 16 in
`tests/test_workflow_agent_profiles.py`; TASK-12 QA artefacts under
`docs/TASK-12/qa/`.

## Stage 3 — ProviderUsageManager + usage-pool-aware scheduler eligibility (TASK-13)

**Summary:** TASK-13 delivered the shared `ProviderUsageManager`
(`src/symphony/orchestrator/usage.py`) and wired usage into the scheduler
eligibility chain. Scheduling decisions no longer read the old single global
rate-limit state (`_latest_rate_limits` stays as telemetry-only); they
evaluate per-pool usage snapshots.

**Manager invariants (all fail open):**
- `snapshot(refresh)`/`snapshot()`/`evaluate()` with `DEFAULT_CACHE_TTL_S =
  60.0`; TTL expiry and window `resets_at` expiry force a re-probe.
- `evaluate()` -> `READY` when snapshot is `None`, `stale`, or
  `authoritative=False`; -> `WAIT_PROVIDER_USAGE` on `hard_limit_reached`
  or any configured window `used_percent >= cap`. Windows whose `resets_at`
  has passed never block (an old blocking snapshot cannot block forever);
  reset-passed + refresh-failure -> fail open.
- Probe success caches the normalized snapshot; probe failure retains
  last-known telemetry and marks it `stale=True`; no probe / no telemetry ->
  fail open.

**Scheduler wiring (core.py):**
- `_eligibility_usage_decision()` resolves profile + pool via the existing
  `cfg.selection_for_state` (same logic as the dispatch path) and evaluates
  the pool; chain order is ownership -> contract -> usage -> contention
  (`core.py:5572-5580`).
- Quota waits are derived `WAIT_NON_SLOT` state with code
  `waiting_provider_usage` — no persistent pause flags; the decision is
  recomputed per issue every dispatch scan, so a later tick with refreshed
  (under-cap) telemetry clears the wait automatically.
- Running workers are never interrupted: the dispatch scan short-circuits
  `running` issues before eligibility (`core.py:3893-3903`); caps only gate
  new dispatches.

**Stage boundary (as of Stage 3):** no provider probes registered yet —
`USAGE_PROBES` is still empty, so real quota telemetry arrives with Stages
4/5 backend probes. Until then evaluate() always fails open (no snapshot).

**Evidence (TASK-13):** 68 usage-limit tests (27 scheduler +
41 manager/phase-1) — `tests/test_orchestrator_usage_limits.py`,
`tests/test_usage_limits.py`; QA artefacts under `docs/TASK-13/qa/` incl.
`repro-after.md` (permission-denied closure with pytest-cache evidence) and
`runtime-blocked.md` (fresh green run not proven in the ticket worktree).

**Decision log:**
- 2026-08-17 | TASK-12 | Usage modeled per shared pool, not per profile:
  `UsagePoolConfig` + `usage_pools` map; `usage_pool` is a reference only.
  Stricter-than-spec: unsupported pool-entry fields rejected at load
  (matches the `agent_profiles` allowlist pattern).
- 2026-08-17 | TASK-13 | Enforcement delivered as derived scheduler state:
  quota waits are `WAIT_NON_SLOT`/`waiting_provider_usage`, recomputed each
  tick (auto-clear), never the operator pause mechanism; running workers
  short-circuit eligibility. Deviation from the ticket description:
  `_latest_rate_limits` retained as telemetry-only (no AC required removal).

**Last updated:** 2026-08-17 by TASK-13 Document.
