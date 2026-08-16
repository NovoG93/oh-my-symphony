# Named agent profiles — Phase 2 runtime resolution

**Summary:** TASK-5 delivered the Phase-2 runtime layer for named agent
profiles: a frozen `AgentSelection` value type, the 8-tier
`selection_for_state` resolver, the `resolve_agent_config` overlay that
produces a concrete backend config per dispatch, `BackendInit` plumbing so
every backend driver receives the resolved config, and per-stage-transition
re-resolution in the orchestrator. CLI flag injection (Phase 3, TASK-6)
and web UI profile editing (deferred post-feature, plan §16) were out of
scope for this phase.

**Resolution model (`src/symphony/workflow/config.py`):**
- `AgentSelection(kind, profile)` — `@dataclass(frozen=True)`; `profile`
  defaults to `None` (kind-only selection, legacy behaviour).
- `selection_for_state(state, *, ticket_profile, ticket_kind,
  dispatch_profile, dispatch_kind, agent_profiles)` implements the §3
  precedence: explicit dispatch profile > explicit dispatch kind > ticket
  `agent_profile` > ticket `agent_kind` > `agent.stage_profiles[state]` >
  `agent.stage_kinds[state]` > `agent.default_profile` > `agent.kind`.
  `dispatch_*` parameters exist for Phase-3 CLI plumbing; no caller passes
  them yet.
- Ambiguity guard: a ticket setting both `agent_kind` and `agent_profile`
  raises `ConfigValidationError` before any tier lookup. The guard also
  lives in the orchestrator helper `_config_for_issue_agent`
  (`src/symphony/orchestrator/helpers.py`), so the reject happens wherever
  the ticket is read, not only inside the resolver.
- Unknown profile names in tiers 1/3/5/7 raise `ConfigValidationError`;
  unmapped states fall through the tiers down to `agent.kind`.

**Overlay (`src/symphony/workflow/profiles.py`):**
- `resolve_agent_config(cfg, selection) -> ResolvedAgentConfig` looks up the
  profile, checks `profile.kind == selection.kind` (mismatch raises), then
  copies only **non-null fields allowed by `PROFILE_FIELDS_BY_KIND`** onto
  the global backend config via `dataclasses.replace(base, **overrides)`.
- Everything not overridden — global `command`, un-overridden timeouts —
  is inherited from the base config; the inherited `command` is never
  clobbered by a profile that does not set one.
- `selection.profile is None` short-circuits to the base config unchanged
  (backward compatibility path).

**BackendInit contract (`src/symphony/backends/__init__.py`):**
- `BackendInit` gained `selection: AgentSelection | None` and
  `resolved_backend_config: Any | None`; `__post_init__` defaults both from
  `cfg` / `resolve_agent_config`, so existing callers that pass neither keep
  working. Drivers consume `resolved_backend_config` via isinstance guards
  (8 drivers updated); flag construction is unchanged (Phase 3).

**Lifecycle re-resolution (`src/symphony/orchestrator/core.py`):**
- The F-01 stage-backend reroute (core.py:6989) re-runs
  `_config_for_issue_agent(base_cfg, issue)` on every stage transition from
  the unrouted `base_cfg` — selection is re-evaluated per transition, never
  carried over from the previous stage's lane.
- `_rebuild_backend_for_phase` re-resolves against `base_cfg` for the new
  phase's target state.

**Backward compatibility:** for profile-less configs, tiers 4/6/8 reproduce
the legacy `kind_for_state` chain (ticket pin > `stage_kinds` > `kind`)
exactly, and `_config_for_issue_agent` returns the identical config for the
identical kind. No `Issue(...)` call site constructs positionally, so the
inserted `agent_profile` frontmatter field breaks nothing.

**Known gap (follow-up material):** the stall reconciler
(`_stall_timeout_ms_for_entry`, core.py:10110) reads the kind's base
`backend_timeouts()`, not the profile-overlaid `stall_timeout_ms` —
adjudicated out of AC scope in TASK-5 Verify.

**Evidence:** 23 unit + lifecycle tests in
`tests/test_workflow_agent_profiles_runtime.py` (8 tier tests, overlay,
BackendInit, ambiguity, backward-compat, plus a real `_run_agent_attempt`
lifecycle test asserting two rebuilt backends with different selections).
TASK-5 QA artefacts under `docs/TASK-5/qa/` (ac-scorecard,
pytest-cache-evidence, security-review, merge-preflight, runtime-blocked).

**Decision log:**
- 2026-08-15 | TASK-5 | 8-tier precedence implemented verbatim from plan
  §3; tickets with both `agent_kind` and `agent_profile` are rejected as
  ambiguous, not first-wins.
- 2026-08-15 | TASK-5 | Overlay copies only allowlisted non-null profile
  fields via immutable `dataclasses.replace`; the global command and all
  un-overridden fields inherit unchanged.
- 2026-08-15 | TASK-5 | `BackendInit` defaults `selection` and
  `resolved_backend_config` in `__post_init__` so all pre-existing callers
  and tests remain source-compatible.
- 2026-08-15 | TASK-5 | Re-resolution happens per stage transition from
  `base_cfg` (F-01, core.py:6989) — profile state is never reused across
  lanes.
- 2026-08-15 | TASK-5 | Live pytest/merge-tree are denied in the ticket
  worktree; the accepted durable evidence pattern is `.pytest_cache`
  `lastfailed={}` + nodeids-count vs the worker-recorded run totals (see
  `docs/TASK-5/qa/pytest-cache-evidence.md`).

- 2026-08-16 | TASK-8 | Scope note corrected: Phase 5 was documentation +
  E2E validation, not UI; web UI profile editing is deferred post-feature
  (plan §16) — see [[agent-profiles-validation-and-docs]].

**Last updated:** 2026-08-16 by TASK-8 Document.
