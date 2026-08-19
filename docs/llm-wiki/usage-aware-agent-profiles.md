# Usage-aware agent profiles — Stages 1-6 (model, probes, enforcement, exhaustion, projection/UI, test suite & docs)

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
*(Superseded by TASK-14 — Stage 2.1 registered the Codex probe, see below.)*

**Evidence (TASK-13):** 68 usage-limit tests (27 scheduler +
41 manager/phase-1) — `tests/test_orchestrator_usage_limits.py`,
`tests/test_usage_limits.py`; QA artefacts under `docs/TASK-13/qa/` incl.
`repro-after.md` (permission-denied closure with pytest-cache evidence) and
`runtime-blocked.md` (fresh green run not proven in the ticket worktree).

## Stage 2.1 + Stage 4 — authoritative Codex probe + runtime provider exhaustion (TASK-14)

**Summary:** TASK-14 delivered the authoritative Codex usage probe (Stage
2.1) and runtime provider-exhaustion classification (Stage 4). Real quota
telemetry now flows into the shared `ProviderUsageManager`, and genuine
plan/credit exhaustion is a capacity wait — it never burns the retry
budget.

**Probe & normalization (`src/symphony/backends/codex.py`):**
- `normalize_codex_rate_limits` (`codex.py:278`): windows keyed by
  `windowDurationMins` — 300 -> `five_hour`, 10080 -> `weekly`, any other
  N -> `<N>_minutes`; the primary/secondary position fields are ignored.
  `resetsAt` parsed from epoch seconds/ms or ISO strings
  (`_parse_resets_at`); hard limit via `rateLimitReachedType` /
  `hardLimitReached` / `rateLimitReached`.
- Auth mode: `authMode`/`accountType` == apiKey marks the snapshot
  `authoritative=False` — ChatGPT subscription caps only bind
  subscription-authenticated Codex; API-key dispatch is never blocked by
  caps (telemetry still recorded).
- `CodexUsageProbe` (`codex.py:431`): `fetch_usage()` calls
  `account/rateLimits/read` (plus `account/read` for the auth mode) via
  the JSON-RPC client, the backend, or a standalone `codex app-server`
  subprocess; any error fails open (returns `None`).
- Registration: `USAGE_PROBES["codex"] = CodexUsageProbe` at codex.py
  import AND lazy import inside `get_usage_probe` (`usage.py`) — both
  idempotent; the registry is no longer empty.

**Notification → cache (immediate):** `account/rateLimits/updated`
notifications normalize the payload and call
`usage_manager.set_snapshot(pool_id, snapshot)` right away
(`codex.py:1113-1157`); the orchestrator also normalizes rate-limits
payloads inside `_on_codex_event` (belt and braces). Notification payloads
carry no authMode, so their snapshots default `authoritative=True`
(telemetry-accurate; cap-authoritative only after a probe refresh).

**Exhaustion classification (Stage 4):**
- `_is_genuine_provider_exhaustion` (`codex.py:398`): RPM/TPM/requests-per-
  minute errors -> False (normal retry path); plan-limit / quota / credit
  keywords -> True.
- `_raise_for_terminal_status` (`codex.py:927`): on a genuine-exhaustion
  turn failure, emits `EVENT_PROVIDER_USAGE_EXHAUSTED` with `pool_id` /
  `resets_at` and raises `ProviderCapacityError` (backends/__init__.py:58;
  event constant at :50). Generic 429/RPM/network errors keep the existing
  `EVENT_TURN_FAILED`/`TurnFailed` handling.

**Orchestrator wiring (`src/symphony/orchestrator/core.py`):**
- `_on_codex_event` (`core.py:8961`): the exhaustion event writes a
  `hard_limit_reached=True` snapshot to `_usage_manager`, sets the
  `RunningEntry` flags (`hit_provider_usage_exhausted`, pool id,
  resets_at), and cancels the worker task.
- `_run_agent_attempt` (`core.py:7392`): catches `ProviderCapacityError`,
  updates the shared pool snapshot, and returns with outcome
  `provider_usage_exhausted` — no generic error escalation.
- `_on_worker_exit_impl` (`core.py:9874`): the exhaustion branch pops
  retry trackers and the claim WITHOUT consuming an attempt count and
  without setting pause flags; the next scheduler tick derives
  `waiting_provider_usage` eligibility from the hard-limit snapshot and
  auto-clears it when the window `resets_at` passes.

**Evidence (TASK-14):** 15 tests in `tests/test_codex_usage.py` (normal-
ization by duration, duration-not-position, multiple limit ids, updated
notification, unknown window, hard limit, api-key no-cap, probe read,
fails open, registry, event+dataclass, genuine vs RPM, no-retry-consumed,
generic 429 normal retry). Pytest cache of the implementation run (20:37
UTC): 2514 collected, `lastfailed = {}`; a fresh re-run was not proven in
the ticket worktree (execution denied — `docs/TASK-14/qa/runtime-blocked.md`).

## Stage 2.2-2.8 — remaining backend usage probes + explicit-pool delegation (TASK-15)

**Summary:** TASK-15 completed the probe lineup for the eight backend
kinds: AGY (authoritative read-only `/quota`), Claude (passive/cached
subscription telemetry), OpenCode / Pi / Prime Agent (delegation to an
explicitly bound `usage_pool`; local estimates never block), Gemini and
Kiro (hard-limit detection only). Every probe honors the fail-open
invariant: no authoritative telemetry -> never block scheduling.

**Probes & normalizers:**
- AGY (`src/symphony/backends/agy.py`): `AgyUsageProbe` executes the
  read-only `agy -p /quota --output-format json` subprocess; non-zero exit,
  non-dict JSON, or exception -> `None` (fail open). `normalize_agy_usage`
  keeps provider/model-specific bucket keys verbatim as window keys — no
  fabricated `five_hour`/`weekly` structure; hard limit via
  `rateLimitReached*` flags.
- Claude (`src/symphony/backends/claude_code.py`): `ClaudeUsageProbe` is a
  passive cached adapter — returns the cached telemetry snapshot or `None`
  on cold start; `normalize_claude_usage` maps `five_hour` variants ->
  `five_hour`, `seven_day`/`7d`/`weekly` variants -> `weekly`;
  `_is_genuine_claude_exhaustion` excludes rpm/tpm/429 and flags
  usage-limit keywords; zero new HTTP (no undocumented Anthropic
  endpoints).
- OpenCode (`src/symphony/backends/opencode.py`):
  `normalize_opencode_local_usage` hardcodes `authoritative=False` — local
  estimates can never block scheduling (`evaluate()` -> READY,
  `orchestrator/usage.py:164`); `OpenCodeGoUsageProbe` registered for both
  `opencode` and `opencode-go`, returns non-authoritative snapshot or
  `None`; `_is_genuine_opencode_exhaustion` for runtime errors.
- Pi / Prime Agent (`src/symphony/backends/pi.py`):
  `_is_genuine_pi_exhaustion` plus exhaustion hooks in terminal-stop
  checks and exit hooks. Runtime pool fallback is the agent's own kind
  (`per_turn.py:94-96`) — an omitted `usage_pool` never implies another
  backend's pool. (TASK-18 moved the Copilot probe out of this module.)
- Copilot (`src/symphony/backends/copilot.py`): `CopilotUsageProbe`
  (percentage unknown — hard-limit detection only, fails open);
  `_is_genuine_copilot_exhaustion` excludes rpm/tpm, flags quota/credit
  keywords. Canonical source `copilot`; legacy `github-copilot` resolves
  via `USAGE_SOURCE_ALIASES`. See [[copilot-backend]].
- Gemini (`src/symphony/backends/gemini.py`): `GeminiUsageProbe` returns
  cached/`None` — no pseudo-TTY scraping of `/stats`;
  `_parse_gemini_exhaustion` extracts reset times from ISO
  "resets at ...", "retry after Ns", or "resets in Nm"; generic 429 is
  classified as not-exhaustion (normal retry).
- Kiro (`src/symphony/backends/kiro.py`): `normalize_kiro_usage` computes
  `used/total*100` into a `monthly` window; `KiroUsageProbe` fails open
  (no programmatic quota endpoint, no interactive TTY scraping);
  `_is_genuine_kiro_exhaustion` flags credit/monthly keywords, excludes
  rpm/tpm.

**Delegation invariant (AC3):** backend `kind` never implies `usage_pool`.
Profiles bind it explicitly (`pi-codex -> codex`, `pi-copilot ->
copilot`, `opencode-codex -> codex`; legacy `github-copilot` still
resolves via `USAGE_SOURCE_ALIASES`); omitted `usage_pool` falls
back to the agent's own kind, never another backend's pool. Runtime
exhaustion hooks use `self._usage_pool or self._agent_name` so
`ProviderCapacityError`/`EVENT_PROVIDER_USAGE_EXHAUSTED` target the bound
pool.

**Registry:** `get_usage_probe` (`src/symphony/backends/usage.py`) lazily
resolves `codex`, `claude`, `agy`, `gemini`, `kiro`, `opencode(-go)`,
`copilot` (`USAGE_SOURCE_ALIASES` normalizes legacy `github-copilot` ->
`copilot` first, usage.py:41/51); all probes are also eager-registered at
module import
(dual registration, idempotent). Missing/unsupported sources return `None`
(fail open).

**Evidence (TASK-15):** 36 tests in `tests/test_backend_usage_probes.py`
(28 named + 8 parametrized `test_usage_probe_failure_never_prevents_
dispatch[...]` across all 8 kinds). Recorded implementation run (21:04
UTC): 2549 collected, `lastfailed = {}` — all 36 + 83 other usage tests +
4 pyright-gate tests unfailed; a fresh re-run was not proven in the ticket
worktree (execution denied — `docs/TASK-15/qa/runtime-blocked.md`).

## Stage 5 — per-pool API projection + Provider Usage UI card (TASK-16)

**Summary:** TASK-16 projected the usage pools through the API and the web
UI. Runtime quota telemetry is now visible per pool on the board: the
orchestrator snapshot carries `provider_usage` and the workflow payload
carries the configured `usage_pools`; the web app renders a Provider Usage
card next to Agent Policy.

**Projection (`src/symphony/orchestrator/core.py:2883`):**
- `_provider_usage_projection()` (`core.py:2891-2969`) emits per-pool
  `source`, `windows` (`used_percent`, `remaining_percent`, `resets_at`
  ISO-8601), `status` (`available` | `capacity_paused` | `unavailable`),
  `stale`, `authoritative`. Pool ids = union of `cfg.usage_pools` keys and
  manager snapshot keys; no per-request network I/O (TTL-cached snapshots).
- `remaining_percent` defaults to `100 - used_percent`; `authoritative`
  defaults True when no snapshot exists (fail-open invariant: unknown usage
  never blocks the UI or scheduler). `rate_limits` is retained as legacy
  telemetry alongside `provider_usage`.

**API payloads (`src/symphony/webapi.py:748`):**
- `_workflow_payload` serializes `usage_pools` as `{pool: {source, caps}}`.
- `_PUBLIC_SCHEDULE_REASONS` gains `waiting_provider_usage` ("waiting for
  provider capacity"); `handle_board` exposes `provider_usage` in the board
  payload (`.get("provider_usage", {})` — stub-orchestrator safe).

**UI card (`src/symphony/web/static/app.js:2814`):**
- `buildProviderUsageCard(usagePools, providerUsage)` renders per-pool
  header + status badge (`Available` / `Capacity paused` / `Usage
  unavailable`), stale/estimated chips, progress bar with `--paused` /
  `--estimated` modifier classes, and meta rows (used %, remaining %,
  configured cap, resets-at / available-after). Mounted after
  `buildAgentPolicyCard` in the workflow editor (app.js:2699) and on the
  Settings page (app.js:4881).
- `scheduleReasonLabel` maps `waiting_provider_usage` -> localized reason
  (app.js:1336). EN/KO i18n blocks: 21 keys each (i18n.js:537-560 /
  1114-1137), parity verified; `scripts/check_i18n.py` green.
- CSS: `.provider-usage-card` + bar/badge/chip rules at style.css:2217-2245
  (no new page).

**Evidence (TASK-16):** 8 Stage 6.12 contract tests in
`tests/test_webapi.py` + `tests/test_web_static_contract.py` (usage_pools
shape + content, snapshot provider_usage, remaining=100-used, card exists,
waiting_provider_usage translation, unknown renders, estimated visually
distinguished). Recorded implementation run (21:34 UTC): 2557 collected,
`lastfailed = {}`; a fresh re-run was not proven in the ticket worktree
(execution denied — `docs/TASK-16/qa/runtime-blocked.md`).

## Stage 6 — comprehensive test suite + final documentation (TASK-17)

**Summary:** TASK-17 closed the feature: the consolidated Stage 6 test suite
(13 areas, 6.1-6.13) and user documentation. The 8-file usage selection
counts 286 nodeids (green in the recorded run, zero `lastfailed`); the
global fail-open invariant is now a permanent parameterized regression test
across all 8 backend kinds; the profile-vs-pool boundary is documented for
users.

**Test suite (Stages 6.1-6.13):**
- 6.1 configuration (`tests/test_usage_limits.py`,
  `tests/test_workflow_agent_profiles.py`): `UsagePoolConfig` fields, shared
  pools per kind, wrapper binding (`pi`/`opencode`/`prime-agent`),
  invalid-cap-percent rejection, unknown-pool rejection, backward
  compatibility when `usage_pools` is omitted, generic windows.
- 6.2 generic pools (`tests/test_usage_limits.py` — added in TASK-17):
  same-pool profiles share one cap; independent pools never cross-block;
  any configured window can block (`five_hour`/`weekly`/`daily`/`monthly`,
  parametrized); non-authoritative estimates never block.
- 6.3-6.9 backend probes (`tests/test_codex_usage.py`,
  `tests/test_backend_usage_probes.py`): per-backend normalization,
  fail-open on probe error, genuine-vs-transient exhaustion (Stages
  2.1/2.2-2.8, sections above).
- 6.10-6.11 scheduler eligibility + running-worker semantics
  (`tests/test_orchestrator_usage_limits.py`): at-cap blocks, below-cap
  allows, reset auto-clear, stale-refresh fail-open, caps never cancel
  running workers, exhaustion never burns the retry budget.
- 6.12 API/UI contract (`tests/test_webapi.py`,
  `tests/test_web_static_contract.py`, `tests/test_i18n.py`): `usage_pools`
  in the workflow payload, `provider_usage` in the orchestrator snapshot,
  `remaining_percent = 100 - used_percent`, Provider Usage card, EN/KO
  `waiting_provider_usage` translation.
- 6.13 fail-open invariant:
  `test_usage_probe_failure_never_prevents_dispatch` parameterized over all
  8 kinds (`codex`, `claude`, `agy`, `gemini`, `kiro`, `opencode`, `pi`,
  `prime-agent`) in both `test_backend_usage_probes.py` and
  `test_orchestrator_usage_limits.py` (16 nodeids): a raising probe + no
  snapshot makes `_eligibility_usage_decision` return `None` (not
  usage-blocked) for every kind.

**Docs:** README.md "Usage Pools & Quota Management" states the core
boundary — profiles define HOW an agent runs (model, reasoning effort,
command, timeouts), pools define WHETHER the provider may start new work
(quotas, caps, reset times); both WORKFLOW examples document the
`usage_pools:` block and the `usage_pool:` profile reference (including a
`pi-codex` multiplexing example); `docs/features/agent-profiles.md` records
the Stage 6 suite and the permanent fail-open invariant.

**Evidence (TASK-17):** 8 new Stage 6.2 tests in `tests/test_usage_limits.py`
(5 defs + 1 parametrized x4); recorded full-suite run (2026-08-17 21:54
UTC): 2564 collected, `lastfailed` absent = zero failures; the 8-file usage
selection counts exactly 286 nodeids. Fresh re-run not proven in the ticket
worktree (execution denied — `docs/TASK-17/qa/runtime-blocked.md`,
`docs/TASK-17/qa/test-cache-evidence.md`); merge preflight clean
(`docs/TASK-17/qa/merge-preflight.md`). Wiki Stage 6 write-back, CHANGELOG
entry, and README.ko.md drift fix delivered by the TASK-17 Document lane.

**Decision log:**
- 2026-08-17 | TASK-17 | Stage 6 consolidated the safety net and docs: 8 new
  generic-pool tests (shared caps, no cross-blocking, any-window blocking,
  estimates never block); the 8-kind fail-open invariant is a permanent
  parameterized regression test (16 nodeids across 2 modules) proving a
  probe failure can never prevent dispatch; 286 usage nodeids green in the
  recorded run; README + both WORKFLOW examples + feature doc state the
  HOW/WHETHER boundary; CHANGELOG Unreleased entry added at Document.
- 2026-08-17 | TASK-12 | Usage modeled per shared pool, not per profile:  `UsagePoolConfig` + `usage_pools` map; `usage_pool` is a reference only.
  Stricter-than-spec: unsupported pool-entry fields rejected at load
  (matches the `agent_profiles` allowlist pattern).
- 2026-08-17 | TASK-13 | Enforcement delivered as derived scheduler state:
  quota waits are `WAIT_NON_SLOT`/`waiting_provider_usage`, recomputed each
  tick (auto-clear), never the operator pause mechanism; running workers
  short-circuit eligibility. Deviation from the ticket description:
  `_latest_rate_limits` retained as telemetry-only (no AC required removal).
- 2026-08-17 | TASK-14 | Authoritative probe + exhaustion classification:
  windows normalized by `windowDurationMins` (300 -> `five_hour`, 10080 ->
  `weekly`, other -> `<N>_minutes`), never by primary/secondary position;
  apiKey auth marks snapshots `authoritative=False` so ChatGPT subscription
  caps bind only subscription-auth; genuine plan/credit exhaustion emits
  `EVENT_PROVIDER_USAGE_EXHAUSTED` + raises `ProviderCapacityError` and
  bypasses the retry budget (ticket returns to `waiting_provider_usage` on
  the next scheduler tick). Probe registration is dual: module-level
  `USAGE_PROBES["codex"]` in codex.py plus lazy import in
  `get_usage_probe` (idempotent).
- 2026-08-17 | TASK-15 | Remaining probes delivered fail-open: AGY executes
  the read-only `agy -p /quota --output-format json` and preserves
  provider/model bucket keys verbatim (no fabricated 5h/weekly structure);
  Claude is a passive cached adapter (five_hour/weekly normalization, no
  new HTTP); OpenCode/Pi/Prime never imply a pool — explicit `usage_pool`
  binding only, local estimates hardcoded `authoritative=False`; Gemini and
  Kiro detect hard limits from runtime errors only with reset extraction,
  no pseudo-TTY / interactive scraping.
- 2026-08-17 | TASK-16 | Stage 5 projected per-pool: orchestrator snapshot
  gains `provider_usage` (source/windows/status/stale/authoritative;
  `remaining_percent = 100 - used_percent` fallback; `authoritative` fails
  open to True) alongside legacy `rate_limits`; workflow payload exposes
  configured `usage_pools` (source + caps); Provider Usage card + paused
  "Capacity paused"/"Available after" state + `waiting_provider_usage`
  schedule reason localized EN/KO; 8 Stage 6.12 contract tests.

**Last updated:** 2026-08-17 by TASK-17 Document.
