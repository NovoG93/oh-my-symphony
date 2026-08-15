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
  columns are persisted provenance; UI surfacing is Phase 5.

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

**Known gaps (follow-up material):**
- Lease-reacquire path (`core.py:2431`) passes only `agent_kind` — a
  reacquired run row drops profile/model/reasoning_effort (LOW-2).
- `graphify-out` symlink committed on the branch (LOW-1); recommend
  excluding it at merge.
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

**Remaining:** Phase 5 — web UI surfacing of profiles and run-record fields.

**Last updated:** 2026-08-15 by TASK-7 Document.
