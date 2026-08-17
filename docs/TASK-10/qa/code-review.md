# TASK-10 QA — Static code review against acceptance criteria

**What**: Line-by-line verification of the diff against the 6 acceptance criteria.
**Why**: Live execution is policy-blocked (see [runtime-blocked.md](runtime-blocked.md)); this review, together with [test-run-evidence.md](test-run-evidence.md), is the verification basis.
**As-Is -> To-Be**: Round-1 review of commit `4b6d556` -> Round-2 re-review of rewritten commit `16df564` with additional cross-checks.

## Round 2 re-verification (2026-08-17, Verify)

The branch was rewritten between rounds (round-1 cited commit `4b6d556`;
HEAD is now `16df564`, 2026-08-17 17:34:30Z, parent = fork point `62a5734`).
The full `git diff 62a5734 HEAD` was re-read this pass. The hunks are
identical to round 1 at the same line numbers:

- `core.py:2434-2442` — lease-reacquire `acquire_run` now passes
  `agent_profile/model/reasoning_effort` (closes wiki LOW-2).
- `core.py:6434-6443` — `dispatch` log emits the three fields (AC1).
- `core.py:7039-7118` — reroute condition + log fields + run-record update (AC2-AC4).
- `run_registry.py:605-644` — `update_stage_agent_profile` (AC4).

Additional cross-checks performed this pass (not in round 1's artifact):

- `_registry_guard` (`core.py:1032-1047`) fails open and never raises — the
  new UPDATE call is wrapped with it, so a broken registry degrades, matching
  every other registry call in the loop.
- `_rebuild_backend_for_phase` (`core.py:7736-7741`) already resolves
  `selection_for_state` + `resolve_agent_config` to build the backend — the
  transition block reads the *same* resolution for its log and UPDATE, so the
  log always agrees with the backend that actually runs (AC5).
- Columns exist: migration 009 (`migrations.py:582-589`) added
  `agent_profile`/`model`/`reasoning_effort`; `state`/`agent_kind`/
  `updated_at` come from the 001 baseline. No schema change needed.
- `RunningEntry` carries the three fields (`entries.py:33-35`); the DB row
  mapping fills them on load (`run_registry.py:2494-2496`), so `from_*`
  values are correct on the next transition.
- Test soundness traced against the worker loop: post-turn refresh sets the
  new state, `if state not in active: break` (`core.py:7550`) exits before a
  terminal state runs the transition block, and `complete_run` writes the
  final title-cased state — which is exactly what the new persistence test
  asserts (`rec.state == "Done"` + profile/model from the last active
  transition).
- develop gained 2 fix commits (`c0348ae`, `b6b1c48`); the only path both
  sides changed since the fork point is `docs/llm-wiki/INDEX.md` at disjoint
  hunks — see [merge-tree-evidence.md](merge-tree-evidence.md).

## AC1 — dispatch log includes agent_profile / model / reasoning_effort

`src/symphony/orchestrator/core.py:6434-6443` — the `dispatch` log now emits
`agent_kind`, `agent_profile=entry.agent_profile`, `model=entry.model`,
`reasoning_effort=entry.reasoning_effort`. The values come from the
`RunningEntry` dataclass fields (`src/symphony/orchestrator/entries.py:25-34`),
which are populated at dispatch from `selection_for_state` +
`resolve_agent_config` (`core.py:6201-6216`). The lease-reacquire call
(`core.py:2434-2442`) gained the same three fields additively — consistent
with "every dispatch".

**Proves**: the fields are emitted at every dispatch/reacquire call site.
**Does not prove**: runtime rendering of the log line (needs a live run).

## AC2 — reroute fires on same-backend profile change

`core.py:7077-7082` — the guard is now
`from_kind != to_kind or from_profile != to_profile or from_model != to_model or from_reasoning_effort != to_reasoning_effort`.
A claude/reviewer -> claude/documenter transition differs only in
`from_profile`/`to_profile` (and model), so the line now fires where the old
`phase_cfg.agent.kind != cfg.agent.kind` guard stayed silent.

**Proves**: the condition structurally covers the same-backend profile change.
**Does not prove**: end-to-end firing in a live board (needs a live run).

## AC3 — reroute log carries from/to profile, model, reasoning_effort

`core.py:7083-7096` — the log includes `from_kind`, `to_kind`,
`from_profile`, `to_profile`, `from_model`, `to_model`, `to_reasoning_effort`
alongside `from_state`/`to_state`.

**Proves**: all required fields are passed to the log call.
**Does not prove**: structured-log serialization of those fields (needs a live run).

## AC4 — run record reflects current stage after each transition

- In-memory: `core.py:7099-7102` updates `running_entry.agent_kind/profile/model/reasoning_effort`
  on every stage transition, so the *next* transition's `from_*` values are the
  previous stage's resolved values.
- Persisted: `core.py:7103-7118` calls
  `RunRegistry.update_stage_agent_profile` (`run_registry.py:605-644`) via
  `_registry_guard` (fails open with an error log, never raises).
- The UPDATE sets `state`, `agent_kind`, `agent_profile`, `model`,
  `reasoning_effort`, `updated_at` and fences with
  `status = 'active' AND owner_pid = ? AND owner_boot_id = ?` — the same
  owner-fencing pattern as the existing checkpoint UPDATE
  (`run_registry.py:575-600`). Columns exist via migration 009
  (`migrations.py:582-589`); no schema change was needed.

**Proves**: the DB write path exists and targets the correct columns with
owner fencing.
**Does not prove**: an observed row transition in `.symphony/state.db` from a
live run (no state.db exists in this workspace; needs a live run).

## AC5 — no routing / precedence change; legacy unaffected

The transition code resolves via the *same* functions the dispatch path and
the backend builder use (`selection_for_state` from `workflow/config.py`,
`resolve_agent_config` from `workflow/profiles.py:82`) — no precedence
constants, default resolution, or backend construction were touched; the diff
only *reads* resolution output. Legacy workflows (no profiles):
`to_profile`/`to_model`/`to_reasoning_effort` resolve to `""` and equal the
`from_*` values, so the reroute condition reduces to the old kind-change
check — no new log lines, no behavior change.

**Proves**: diff contains no edits to resolution, construction, or routing
code paths.
**Does not prove**: runtime equivalence for every legacy config permutation
(needs a live run).

## AC6 — tests exist and were collected

- `tests/test_run_registry.py::test_run_registry_update_stage_agent_profile` — direct persistence unit test.
- `tests/test_workflow_agent_profiles_runtime.py::test_dispatch_logs_profile_model_reasoning_effort` — AC1.
- `...::test_stage_backend_rerouted_logs_same_kind_different_profile` — AC2/AC3, same-kind claude reviewer->documenter.
- `...::test_orchestrator_stage_transition_persists_profile_to_run_record` — AC4 end-to-end through `_run_agent_attempt`.
All four were collected by the 18:13 suite collection recorded in
[test-run-evidence.md](test-run-evidence.md).

## Orphan scope check

- `docs/index.html:166` version badge `v0.20.1 -> v0.21.0`: not required by
  the ticket, but consistent with `pyproject.toml` version `0.21.0` (release
  0.21.0 is in this branch's ancestry). Single line, no functional impact —
  noted as LOW, not blocking.
- All other delta paths (`core.py`, `run_registry.py`, two test files,
  `docs/TASK-10/work/details.md`, the two wiki files) are ticket scope.
