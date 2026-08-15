# TASK-4 Verify — Review notes (retry attempt 1, 2026-08-15T18:50Z)

Clean review of HEAD (050d96f) vs merge-base 4c9e7b1. The two findings from
the previous pass's `## Review Findings` are both resolved in the tree:

## Finding resolution

- **MEDIUM (duplicate profile names not rejected)** — FIXED. Guard at
  `src/symphony/workflow/builder.py:1011-1015` raises `ConfigValidationError`
  when a stripped name is already in `out`; unit test
  `tests/test_workflow_agent_profiles.py:375` covers whitespace-colliding
  keys (`"qa"` vs `" qa"` -> "duplicate profile name 'qa'"); the PyYAML
  exact-duplicate-collapse boundary is documented in
  `docs/TASK-4/work/details.md` ("Parser & Builder Boundaries").
- **LOW (machine-specific `graphify-out` symlink)** — FIXED. `git show
  HEAD:graphify-out` exits 128 (`path 'graphify-out' does not exist in
  'HEAD'`); `git diff 4c9e7b1 HEAD --stat` shows no tree entry for it.

## Scope check (no orphan scope)

- Full diff: 10 files, 950 insertions, 0 deletions vs merge-base:
  4 source files, 1 test file, 5 docs/TASK-4 evidence files. All
  ticket-scoped (config model + parsing + validation + tests + evidence).
- Runtime dispatch/backends untouched: grep over `src/` shows the new keys
  (`agent_profiles`, `stage_profiles`, `default_profile`) are read only by
  `workflow/config.py`, `workflow/builder.py`, `workflow/constants.py`,
  `workflow/__init__.py`. No backend, orchestrator, or dispatch code
  consumes them (Phase 2/3 scope preserved).
- `AgentConfig` and `ServiceConfig` grow only appended fields with defaults
  — existing positional-call sites keep receiving the same values.
- WORKFLOW.md / .gitignore / scripts/symphony-setup-worktree.sh diffs vs
  the fork are main-side-only (5ddead6, ba392f3); the branch does not
  modify them.

## Allowlist cross-check vs plan (sections 1-5)

`PROFILE_FIELDS_BY_KIND` covers all 8 `SUPPORTED_AGENT_KINDS`; codex allows
model+reasoning_effort, claude allows model but not reasoning_effort, agy
and the others allow neither model nor reasoning_effort; `command` and the
three timeouts allowed everywhere; `resume_across_turns` allowed except
codex. Matches `docs/TASK-4/work/model_design.md` and the plan's
backend-specific validation requirement.

## Verdict

No CRITICAL/HIGH/MEDIUM issue remains in ticket scope. One pre-existing,
branch-unrelated full-suite failure (chat symlink-loop marker test on
Python 3.14) is recorded in `qa/qa-evidence.md`; it is present at the fork
point too (`git diff 4c9e7b1 HEAD -- src/symphony/chat.py
src/symphony/projects.py tests/test_chat.py` is empty) and is outside this
ticket's scope.
