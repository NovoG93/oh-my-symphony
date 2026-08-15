# Named agent profiles — Phase 3 backend execution

**Summary:** TASK-6 delivered the Phase-3 backend-execution layer: Claude
`--model` injection, dispatch selection via `selection_for_state` with a
per-ticket refusal guard, profile-scoped session identity, and 9 backend
unit tests. Note the plan's §6/§9 config-side work (`ClaudeConfig.model`
field at config.py:429, Codex turn params, BackendInit plumbing, per-stage
session rebuild) actually shipped in Phase 2 (TASK-5, main `7b70e09`);
Phase 3's branch delta beyond main is `claude_code.py` (+21), `core.py`
(+17), and the +716-line test file.

**Claude model injection (`src/symphony/backends/claude_code.py`):**
- `_inject_model(command, model)` inserts `--model <shlex.quote(model)>`
  immediately after a literal leading `claude` token; an empty model or a
  wrapper-script command (no leading `claude`) passes through unchanged.
- Applied in `run_turn` before `_inject_add_dirs` (claude_code.py:223-224),
  so operator-authored pipelines and redirections survive injection.

**Dispatch alignment + guard (`src/symphony/orchestrator/core.py` `_dispatch`):**
- `_dispatch` now resolves `AgentSelection` via
  `cfg.selection_for_state(issue.state, ticket_profile=..., ticket_kind=...)`
  (was `cfg.agent.kind_for_state`), so the dispatch registration kind matches
  the resolved profile kind.
- `ConfigValidationError` (ticket setting both `agent_kind` + `agent_profile`,
  or an unknown profile) is caught per ticket: logs
  `dispatch_selection_refused` with `issue_id`/`identifier` and returns
  `False` — the candidate loop marks the ticket refused and keeps
  processing; the scheduler tick never dies.

**Codex params (Phase 2 code, pinned by Phase 3 tests):**
- `_build_turn_params` sends `params["model"]` / `params["effort"]` from the
  resolved `CodexConfig` (codex.py:523-526); command inheritance/override
  handled in `_prepare_command_and_env`.

**Session scoping (Phase 2 rebuild + Phase 3 tests):**
- Per-stage backend rebuild (F-01 reroute / `_rebuild_backend_for_phase`)
  plus session/thread clearing (core.py:7039-7041) give session identity =
  ticket + backend kind + profile: the same backend with a different profile
  starts a fresh session and never resumes another profile's session.
  Cross-ref [[session-persistence]].

**Tests (`tests/test_workflow_agent_profiles_backend.py`, 9 tests):**
- Codex model/reasoning params, Codex+Claude command inheritance/override,
  `_inject_model` edge cases, profile model injected in `run_turn`,
  resume/timeout inheritance, two session-scoping tests, and the
  dispatch-refusal regression.
- Evidence: full-suite pytest-cache session — 2354 collected incl. all 9,
  `lastfailed` empty (indirect; live rerun denied in the worktree) —
  `docs/TASK-6/qa/test-run-evidence.md`.

**Remaining phases:** Phase 4 (run-record/CLI/doctor), Phase 5 (UI).
User-facing key docs: `WORKFLOW.example.md` gained a commented
`agent_profiles:` block in TASK-6's Document pass.
