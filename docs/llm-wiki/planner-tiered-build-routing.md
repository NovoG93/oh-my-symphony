# Planner-driven tiered Build routing (OpenCode free tier + AGY)

**Summary:** TIER-001 shipped the engine/prompt half of tiered Build routing:
the deep `Plan` lane classifies each Build slice and pins a ticket profile, and
lane mutation now keeps `agent.stage_profiles` in sync. The *mechanism*
(named profiles, the 8-tier resolver, `--agent-profile` on the ticket) already
existed — see [[agent-profile-resolution]] and [[agent-profile-config]]. What
is new is the *policy* layer above it plus one mutation bug fix.

**Why tiered routing exists:** small, tightly bounded slices waste a capable
backend, while large or ambiguous slices fail on a weak one. Plan decides per
slice; the orchestrator just resolves whatever profile the ticket names.

**Sizing contract (`docs/symphony-prompts/file/deep/plan.md`):**
- A *minimum useful slice* is one observable behavior, its failing test, and
  one exact proof command.
- Every Build slice is classified `small-free` or `large-capable`.
- `small-free` requires all of: exactly one behavior, no unresolved design,
  `<= 3 files / <= 200 net lines`, one exact verification command, and no
  auth, security, migration, concurrency, release, or cross-service work.
  It is pinned `--agent-profile opencode-free-small`.
- Every other **or uncertain** slice is `large-capable`, pinned
  `--agent-profile agy-builder`. Unknown/uncertain defaults to the capable
  backend, never the free one.
- The older general ceiling still applies on top: `<= 5 files / <= 500 net
  lines` per Build ticket (`contracts.md`).
- Plan is the sole decomposition point: `Max 8 Build tickets` per request, and
  Build workers must not spawn child tickets. Workers that self-decompose
  bypass both the classification and the cap.

**Precedence — the fact that makes ticket pinning work:**
- `selection_for_state` (`src/symphony/workflow/config.py:332`) resolves in 8
  tiers: dispatch profile > dispatch kind > **ticket `agent_profile`** >
  ticket `agent_kind` > **`agent.stage_profiles[state]`** > `agent.stage_kinds`
  > `agent.default_profile` > `agent.kind`.
- Ticket profile is tier 3, stage profile is tier 5 — so a Build ticket pinned
  by Plan outranks the board's `Build` lane default. Tiers 1-4 also exist as
  an escape hatch for an operator overriding one card.
- Pinning a ticket with **both** `agent_kind` and `agent_profile` raises
  `ConfigValidationError` (ambiguity guard) rather than first-wins.

**Only Build tickets get pinned.** QA, Verify, and Document tickets are left
unpinned so `agent.stage_profiles` keeps owning those lanes; pinning them would
silently freeze them onto one backend as the board evolves.

**Two gotchas worth not rediscovering:**
1. **`opencode` profiles cannot carry `model:`.** `PROFILE_FIELDS_BY_KIND`
   (`src/symphony/workflow/constants.py:141`) allows only `command`,
   `resume_across_turns`, the three timeouts, and `usage_pool` — a `model:` key
   raises `ConfigValidationError` at config-build time. Pass the model in the
   command instead: `command: opencode run --format json --auto --model
   <verified-id>`. Free model IDs are live deployment facts, not Symphony
   constants — they rotate, so read them from the provider and never hardcode
   them into shipped prompts.
2. **Per-state maps are edited in two places.** `apply_states_update`
   (`src/symphony/workflow/mutate.py:254`) and `apply_lane_preset`
   (`src/symphony/workflow/mutate.py:611`) each call `_rename_state_keyed_map`
   once per map. Adding a new per-state map means adding a line to **both**.
   `agent.stage_profiles` was missed at both sites, so renames left routes
   pointing at dead states and presets left obsolete keys behind; TIER-001
   fixed that with one line per site and focused tests in
   `tests/test_workflow_mutate.py`.

**Policy vs. enforcement:** the file/line and ticket-count bounds live in
prompt text, so they are *policy the Plan agent follows*, not something the
scheduler validates. Nothing rejects a 400-line Build ticket. Operators wanting
a hard guarantee must enforce it outside the prompt (review, or a future
scheduler check).

**Evidence:** `tests/test_workflow_mutate.py` (rename carries the route,
removal preserves the untouched lane, deep preset leaves `stage_profiles == {}`
and still reloads); `tests/test_workflow_presets.py::test_deep_prompts_are_succinct_and_carry_the_gates`
asserts the eight literal prompt contracts and the `<= 45`-line succinctness
budget. Ticket artefacts under `docs/TIER-001/qa/`.

**Decision log:**
- 2026-09-17 | TIER-001 | Tiering is a *prompt policy* plus ticket-level pins;
  no scheduler validation was added, so bounds are advisory by design.
- 2026-09-17 | TIER-001 | Uncertain classification falls back to AGY
  (`agy-builder`), not to the free profile — an undersized backend failing
  loudly is judged worse than a capable backend doing cheap work.
- 2026-09-17 | TIER-001 | `agent.stage_profiles` is renamed/dropped in lockstep
  with the other per-state maps; the bug was a missing call site, not a design
  gap, so the fix stayed at one line per site.
- 2026-09-17 | TIER-001 | Only Build tickets get planner-selected profiles;
  QA/Verify/Document stay stage-routed so lane defaults keep applying.
- 2026-09-17 | TIER-001 | `deep/plan.md` grew to 26 lines (budget 45); the
  tiering rules were folded into the existing numbered steps rather than added
  as a new section to stay inside the succinctness gate.
- 2026-09-17 | TIER-001 | `README.ko.md` was not updated — it is outside the
  ticket's allowed files and still lacks the tiered-routing subsection.

**Last updated:** 2026-09-17 by TIER-001 Document.
