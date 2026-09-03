# Named agent profiles — Phase 4 observability + tooling

**Summary:** TASK-7 delivered the Phase-4 observability and tooling layer:
run records persist `agent_profile`/`model`/`reasoning_effort` (migration
v9), file-board tickets can pin `agent: {profile: ...}` (flat
`agent_profile:` alias), `symphony board new/update` gained
`--agent-profile`, and `symphony doctor` gained per-profile PASS/FAIL/WARN
checks. 18 new tests; recorded full-suite session 2,371 collected,
`lastfailed` empty (indirect — live pytest is gate-denied in the worktree).

**Run-record provenance (`src/symphony/orchestrator/`):**
- Migration v9 (`migrations.py:582-628`, `RUN_AGENT_PROFILE_VERSION = 9`)
  adds `agent_profile`, `model`, `reasoning_effort` TEXT columns via the
  additive `_add_column_if_missing` pattern.
- `RunRecord` gained keyword-defaulted `agent_profile`/`model`/
  `reasoning_effort`; `RunRegistry.acquire_run` /
  `acquire_continuation_run` / `_record` / `_run_summary` persist and query
  them; `_record` reads the new columns defensively (`row.keys()`) so a
  pre-v9 database read by new code yields `""`.
- Orchestrator: `RunningEntry` carries the metadata; `_run_record_payload`
  supplies it at acquisition; `diagnostics.py` allowlists the three keys on
  the `run_acquired` event.
- `symphony runs` output is unchanged (still prints `agent_kind`) — the new
  columns are persisted provenance; UI surfacing is deferred post-feature
  (plan §16).

**Ticket override (`src/symphony/trackers/file.py`):**
- `create`/`update_fields` write `agent: {profile: <name>}` frontmatter;
  hand-edited flat `agent_profile:` is parsed on read. Both forms coexist
  with legacy `agent.kind`.
- Mutual exclusion: setting both `agent_kind` and `agent_profile` raises
  `SymphonyError("ambiguous agent override: ...")` on create and update;
  `record_agent_kind` preserves an existing `agent_profile` (no clobber).

**CLI (`src/symphony/cli/board.py`):**
- `--agent-profile` on `board new` and `board update` (plus `--agent-kind`
  on update for parity); `--agent-kind` + `--agent-profile` together exit 1;
  empty profile names rejected on `new`.
- Unknown profile names are rejected (exit 1, `available: [...]`) when the
  loaded `WORKFLOW.md` defines `agent_profiles:`; `board show` prints
  `agent: profile=<name>` when set.

**Doctor (`src/symphony/cli/doctor.py` `check_agent_profiles`):**
- Per profile `agent.profile.<name>`: kind in `SUPPORTED_AGENT_KINDS`
  (FAIL), model free of whitespace/newlines (FAIL), command
  `shlex`-parseable and non-empty (FAIL), binary on `$PATH` via
  `shutil.which` (`python` falls back to `sys.executable`; FAIL), profile
  `command` override → WARN, otherwise PASS with model.
- `agent.stage_profiles.<stage>` and `agent.default_profile` resolve against
  `agent_profiles` (PASS/FAIL). Unsupported profile properties never reach
  the doctor: Phase-1 config-build validation rejects them, and the doctor
  surfaces the load failure as `FAIL workflow load failed` (exit 2).

**Per-stage observability (TASK-10, 2026-08-17):**
- `dispatch` log (`core.py:6437-6440`) now emits `agent_profile`, `model`,
  `reasoning_effort` alongside `agent_kind` on every dispatch (including
  reacquired attempts, `attempt_kind="reacquired"`).
- The lease-reacquire `acquire_run` call (`core.py:2434-2440`) now passes
  the same three fields — a reacquired run row keeps profile/model/effort
  (closes LOW-2 below).
- `stage_backend_rerouted` (`core.py:7075-7098`) now fires when kind,
  profile, model, OR reasoning effort differs between stages — a
  same-backend profile change (claude reviewer -> claude documenter) is no
  longer silent — and logs `from_profile`/`to_profile`/`from_model`/
  `to_model`/`to_reasoning_effort` next to `from_kind`/`to_kind`/
  `from_state`/`to_state`.
- `RunRegistry.update_stage_agent_profile` (`run_registry.py:605-644`)
  updates the active owned run row (`status='active'` + `owner_pid` +
  `owner_boot_id` fence) with the new `state` and profile/model/effort on
  every stage transition; `_registry_guard` fails open.
- No precedence/routing change: the transition block reuses the dispatch
  resolution (`selection_for_state` + `resolve_agent_config`), and with no
  profiles the new fields resolve to empty/equal values, so the expanded
  reroute condition degenerates to the old kind-change check.
- 4 new tests: `test_dispatch_logs_profile_model_reasoning_effort`,
  `test_stage_backend_rerouted_logs_same_kind_different_profile`,
  `test_orchestrator_stage_transition_persists_profile_to_run_record`
  (`tests/test_workflow_agent_profiles_runtime.py`),
  `test_run_registry_update_stage_agent_profile`
  (`tests/test_run_registry.py`).

**Known gaps (follow-up material):**
- ~~Lease-reacquire path passes only `agent_kind` — reacquired run row drops
  profile/model/reasoning_effort (LOW-2)~~ — **resolved 2026-08-17 by
  TASK-10** (reacquire `acquire_run` call, `core.py:2434-2440`).
- ~~`graphify-out` symlink committed on the branch (LOW-1)~~ — resolved
  2026-08-17 by develop commit `94a532b` (untracked before the TASK-10
  merge; `.gitignore` entry broadened to match symlinks, trailing slash
  only matched directories).
- Stall-reconciler timeout lookup still ignores profile-overlaid timeouts
  (TASK-5 carry-over, see [[agent-profile-resolution]]).

**Evidence:** 18 tests in
`tests/test_workflow_agent_profiles_tooling.py` (migration, registry
persistence/query, tracker create/update/ambiguity/preserve, CLI new/update/
show/rejections, 7 doctor checks). QA artefacts under `docs/TASK-7/qa/`
(static-review, test-run-evidence, merge-preflight, runtime-blocked).

**Decision log:**
- 2026-08-15 | TASK-7 | Migration v9 is additive and idempotent; RunRecord
  fields are keyword-defaulted so pre-existing positional callers keep
  working.
- 2026-08-15 | TASK-7 | Ambiguous overrides are rejected (CLI exit 1;
  tracker `SymphonyError`), never first-wins — the Phase-2 resolver guard,
  now enforced at the write boundary too.
- 2026-08-15 | TASK-7 | Doctor model check is whitespace + command `shlex`
  parseability only. The regex `^[a-zA-Z0-9_.:/-]+$` quoted in early
  `work/details.md` notes was never implemented (LOW-3) — corrected in the
  Document pass.
- 2026-08-15 | TASK-7 | The board CLI flags anticipated as "Phase-3 CLI
  plumbing" in [[agent-profile-resolution]] landed in Phase 4; they write
  ticket frontmatter via the tracker, and `selection_for_state`'s
  `dispatch_profile`/`dispatch_kind` still have no callers.

- 2026-08-16 | TASK-8 | Phase 5 was documentation + E2E validation, not
  web UI — see [[agent-profiles-validation-and-docs]]. Web UI surfacing of
  profiles and run-record fields remains deferred post-feature (plan §16).

- 2026-08-17 | TASK-10 | Per-stage observability reuses the dispatch
  resolution (`selection_for_state` + `resolve_agent_config`) inside the
  transition block instead of adding a second resolution path — the reroute
  log and run-record UPDATE only *read* resolution output, so precedence
  and backend construction are untouched.
- 2026-08-17 | TASK-10 | Legacy mode stays silent: with no profiles the
  resolved `to_profile`/`to_model`/`to_reasoning_effort` equal the `from_*`
  values, so the expanded reroute condition reduces to the old kind-change
  check — no new log lines or DB writes for `agent.kind`/`stage_kinds`
  workflows.
- 2026-08-17 | TASK-10 | LOW-1 (`graphify-out` symlink) was resolved
  upstream by `94a532b` before the TASK-10 merge, so the merge delivers a
  tree without the symlink; future "known gaps" entries should record
  upstream resolution commits as soon as they appear, not wait for the
  merge.

**Last updated:** 2026-08-17 by TASK-10 Document.
