# TASK-6 Verify — Static Review (scope + security-audit backing)

## Goal

Record the review of the full branch diff against the ticket scope, plan
sections 5/6/9/11, and the Acceptance Tests, plus the static checks backing
the `## Security Audit` rows. Written 2026-08-15 during the first Verify
pass; the pass ended in a rewind for one MEDIUM finding (see
`qa/review-finding-dispatch-raise.md`).

## Diff scope vs ticket scope

Diff `7b70e09..HEAD` (main tip → TASK-6 branch):
`src/symphony/backends/claude_code.py` (+21), `src/symphony/orchestrator/core.py`
(+7), `tests/test_workflow_agent_profiles_backend.py` (+650, new),
`docs/TASK-6/work/*.md` (new), `graphify-out` (symlink, see below). No UI,
run-record, CLI, or doctor changes — matches the ticket's Phase 3 boundary.

- Plan §9 (backends consume resolved config): `CodexAppServerBackend.__init__`
  takes `init.resolved_backend_config` when it is a `CodexConfig` with a
  `cfg.codex` fallback (`src/symphony/backends/codex.py:267-272`); the Claude
  backend does the same for `ClaudeConfig` (`claude_code.py:117-119`).
  `_build_turn_params` forwards `model`/`effort` (`codex.py:523-526`);
  `_prepare_command_and_env` runs the resolved `self._codex.command`
  (`codex.py:351`). These paths are unchanged by this branch (Phase 2 on
  main) — the branch's new tests pin them in place, which is the correct
  Phase 3 move for Codex.
- Plan §6 (first-class Claude model): `ClaudeConfig.model` exists with
  default `""` (`src/symphony/workflow/config.py:429`, Phase 2). This branch
  adds the missing injection half: `_inject_model` (`claude_code.py:75-91`)
  called at the top of `run_turn` before `_inject_add_dirs`
  (`claude_code.py:223-224`).
- Plan §11 (session scoping): per-phase backend rebuild + `RunningEntry`
  session/thread clearing already existed on main (`core.py:7006-7031`,
  `_rebuild_backend_for_phase` at `core.py:7585-7646`, both resolving via
  `selection_for_state`). This branch aligns initial dispatch with that same
  resolution (`core.py:6177-6182`) — with the defect recorded in
  `qa/review-finding-dispatch-raise.md`.
- Tests: 8 new tests map 1:1 to the ticket's Acceptance Tests section
  (AC1–AC6). Assertions checked against implementation:
  `_inject_model` ordering/quoting/wrapper no-op (test lines 220-252) match
  `claude_code.py:75-91`; command inheritance/override match the resolved
  `self._claude.command` / `self._codex.command` wiring; session-scoping
  tests exercise `_run_agent_attempt` with a fake `build_backend` and assert
  distinct per-profile sessions with no cross-resume.
- Orphan scope: none in source. `graphify-out` symlink is branch hygiene
  (see `qa/review-finding-dispatch-raise.md`, LOW row) — removed from the
  working tree this pass.

## Security-relevant static checks (audit backing)

- Command-string injection: `_inject_model` inserts `--model <model>` only
  when the command starts with a literal `claude` token
  (`claude_code.py:84-85`); model values are shell-quoted via
  `shlex.quote` (`claude_code.py:89`), so operator/model strings containing
  spaces or metacharacters cannot break out into extra shell words. Model
  values originate from operator-authored resolved config, not user web
  input. Wrapper scripts without a leading `claude` token are returned
  unchanged (`claude_code.py:84-85`), so no operator command is altered
  without an explicit `claude` token. Pass.
- Input validation: empty/None model strings no-op
  (`claude_code.py:83-84`); the `isinstance` guards on
  `resolved_backend_config` fall back to the global config
  (`codex.py:267-271`, `claude_code.py:117-119`), so a wrong config type
  cannot be consumed. Pass (with the exception-handling gap on ticket
  frontmatter ambiguity recorded as the MEDIUM finding — a robustness
  issue, not a security sink).
- No secret handling, no web/HTML surface, no CSRF/authz/rate-limit code in
  the diff → n/a.

## What this proves / does not prove

Proves: scope conformance and injection safety by direct reading. Does not
prove: runtime behaviour — pytest was not executed this pass (permission
gates deny process execution; review rewinded before QA).

## How to re-run (for the re-verify pass)

```
env -u SYMPHONY_GIT_WRITABLE_ROOTS .venv/bin/pytest tests/test_workflow_agent_profiles_backend.py -v
```
If execution is denied, fall back to `.pytest_cache/v/cache/nodeids` +
`lastfailed` as indirect evidence and record the refusal in
`docs/TASK-6/qa/runtime-blocked.md`.
