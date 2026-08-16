# TASK-4 Verify — Review Findings details (2026-08-15T18:36:49Z, retry attempt 1)

## Finding 1 (MEDIUM): duplicate profile names are not rejected

- **Problem**: AC "Validation rejects: empty/duplicate profile names" is only
  half met. `_validated_agent_profiles` rejects empty/whitespace names, but
  uniqueness is never enforced:
  - Whitespace-normalized collisions reach the builder and silently
    last-win: `agent_profiles: {" qa": {...}, "qa": {...}}` — both keys
    strip to `"qa"` and `out[name] = AgentProfileConfig(...)` overwrites the
    first entry with no error (`builder.py:989` loop, no `if name in out`
    guard before the assignment at `builder.py:1101`).
  - Exact-duplicate YAML keys never reach the builder at all:
    `parser.py:57` uses `yaml.safe_load` (PyYAML, pinned in
    `pyproject.toml:15`), which collapses duplicate mapping keys keeping the
    last — so `qa:` twice parses as one entry with no error.
  - Consequence: malformed config is silently accepted with one profile
    dropped, contrary to the ticket's "fail during validation, not later"
    requirement. No unit test covers either case (15 tests, none for
    duplicates).
- **Evidence**: `git show b816e33:src/symphony/workflow/builder.py`
  (`_validated_agent_profiles`, lines 989-1101) — no membership guard
  before `out[name] =`; `git show b816e33:src/symphony/workflow/parser.py:57`
  — `yaml.safe_load`; `tests/test_workflow_agent_profiles.py` — no
  duplicate-name test.
- **Requested fix (builder-level, in ticket scope)**: in
  `_validated_agent_profiles`, before `out[name] = ...`, raise
  `ConfigValidationError(f"agent_profiles has duplicate profile name {name!r}")`
  when `name in out`. Add unit tests: (a) two keys normalizing to the same
  name (`" qa"` and `"qa"`) -> error; (b) re-run the 15 existing tests.
  Document the boundary in `docs/TASK-4/work/details.md`: exact-duplicate
  YAML keys are collapsed by PyYAML before the builder; rejecting those
  would require a parser-level duplicate-key loader (pattern exists in
  `src/symphony/orchestrator/release_contracts.py:109`) and is outside this
  ticket's Phase-1 scope — flag it to the orchestrator rather than silently
  ignoring.
- **Scope**: `src/symphony/workflow/builder.py` +
  `tests/test_workflow_agent_profiles.py` + one details.md note.

## Finding 2 (LOW, fixed during Verify): machine-specific symlink committed

- **Problem**: commit b816e33 tracked `graphify-out` as a symlink blob whose
  target is the machine-specific absolute path
  `/home/symphony/git/oh-my-symphony/graphify-out` (mode 120000). It entered
  the branch because the fork point predates main's commit 5ddead6, which
  added `graphify-out/` to `.gitignore` and the symlink setup to
  `scripts/symphony-setup-worktree.sh`. Merged, main would ship a dangling
  absolute symlink in every clone, and `.gitignore` does not untrack it.
- **Evidence**: `git ls-tree HEAD graphify-out` -> `120000 blob ... graphify-out`
  with blob content = the absolute path; `git diff main -- .gitignore` shows
  the ignore line exists on main only.
- **Fix applied**: `rm graphify-out` in the worktree (working tree now shows
  `D graphify-out`); the turn's harness auto-commit records the deletion.
  Nothing in the branch references the symlink.
- **Scope**: branch tree only; no source change.

## What was reviewed

- Full diff vs merge-base 4c9e7b1: config.py (AgentProfileConfig frozen
  dataclass, AgentConfig.stage_profiles/default_profile, ServiceConfig
  .agent_profiles — all appended with defaults, positional-call-safe),
  constants.py (PROFILE_FIELDS_BY_KIND for all 8 kinds), builder.py
  (_validated_agent_profiles/_validated_stage_profiles/_validated_default_profile,
  wired into build_service_config), __init__.py exports, 15 new tests.
- No orphan scope in source: the only non-ticket tree entries were the
  graphify-out symlink (fixed above) and docs/TASK-4/work/* (ticket-owned).
- WORKFLOW.md/.gitignore/setup-script diffs vs main are main-side-only
  changes (5ddead6, ba392f3b); the branch does not touch those files.
- No dispatch change: grep shows the new fields are read only by
  config/builder/constants/__init__; no backend/orchestrator code consumes
  them (Phase 2/3 work).
- Runtime QA blocked: pytest (2 forms) and git merge-tree denied by the
  permission policy — see `qa/runtime-blocked.md`. `## QA Evidence` /
  `## AC Scorecard` / `## Merge Status` therefore deferred to the next
  Verify pass after the MEDIUM fix.
