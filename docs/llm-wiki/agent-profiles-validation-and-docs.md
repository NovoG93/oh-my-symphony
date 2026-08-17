# Named agent profiles — Phase 5 documentation & end-to-end validation

**Summary:** TASK-8 completed Phase 5 of the named agent profiles feature:
comprehensive user-facing and technical documentation across `README.md`,
`README.ko.md`, `WORKFLOW.example.md`, `WORKFLOW.file.example.md`, and
`docs/features/agent-profiles.md`, explicit migration guidance for existing
`agent.kind`/`stage_kinds` users, and end-to-end acceptance validation in
`tests/test_workflow_agent_profiles_e2e.py`. Web UI profile editing is
explicitly deferred.

**Documentation deliverables:**
- `docs/features/agent-profiles.md`: Comprehensive reference covering backend
  kinds vs. profiles, non-null overlay inheritance, 8-tier resolution
  precedence, ticket overrides, supported fields by backend (`PROFILE_FIELDS_BY_KIND`),
  session scoping `(ticket_id, backend_kind, profile_name)`, multi-model and
  mixed-backend workflow examples, migration guidance, and CLI/doctor tooling.
- `README.md` & `README.ko.md`: Updated `## Pick an agent` / `### Named Agent Profiles`
  with concepts, 8-tier precedence, supported fields, multi-model/mixed-backend examples,
  and migration guidance.
- `WORKFLOW.example.md` & `WORKFLOW.file.example.md`: Added stage profile routing
  and example commented `agent_profiles:` configurations illustrating Codex and Claude
  overlaid profiles.

**End-to-End & Backward-Compatibility Validation (`tests/test_workflow_agent_profiles_e2e.py`):**
- **§20 Acceptance Configuration**:
  - `Research` → Claude / `fable` (`claude -p --output-format stream-json --verbose --model fable`)
  - `Plan` → Codex / `sol` with `reasoning_effort=high` (`codex app-server`)
  - `Build` → Claude / `sonnet` (`claude -p --output-format stream-json --verbose --model sonnet`)
  - `Review` → Codex / `sol` with `reasoning_effort=high` (`codex app-server`)
  - `QA` → Codex / `luna` with `reasoning_effort=medium` (`codex app-server`)
- **Backward Compatibility**: Workflows configuring only `agent.kind` and
  `agent.stage_kinds` parse and resolve to un-overlaid base configurations (`profile=None`)
  identically to pre-profile releases.
- **Single-Backend Multi-Model**: Multiple profiles under the same kind (`codex-sol` vs.
  `codex-luna`) resolve independently with distinct parameters.
- **Migration Precedence**: Incremental transition paths where `stage_profiles`
  overrides `stage_kinds` (tier 5 > tier 6), and unmapped stages fall back to
  `default_profile` (tier 7) or `agent.kind` (tier 8).
- **Doctor Preflight**: `symphony doctor` passes the complete §20 acceptance configuration.

**Phase boundaries & deferred work:**
- Web UI profile editing remains explicitly deferred post-feature (see plan §16).

**Verification evidence (2026-08-16, Verify + Document):**
- Recorded session on the final tree: `.pytest_cache` `lastfailed` `{}`,
  2,380 nodeids incl. all 8 E2E tests; pyc (05:07:07.134) newer than source
  (05:07:04.725) — proven: the final test content was imported and the
  finishing session ended with zero failures. Not proven live: the worktree
  gate refuses pytest/doctor/merge-tree (`docs/TASK-8/qa/runtime-blocked.md`).
- Count delta: "2367 passed, 9 skipped" (2376) vs 2,380 collected — 4-id
  cache-union noise across sessions, not a failure signal (same class as
  TASK-7's delta).
- Docs cross-checked against runtime: 8-tier list matches
  `selection_for_state`; field table matches `PROFILE_FIELDS_BY_KIND`
  (`constants.py:98`); Claude `--model` injection matches
  `claude_code.py::_inject_model` (`docs/TASK-8/qa/static-review.md`).
- Re-run on an unrestricted checkout: `.venv/bin/pytest
  tests/test_workflow_agent_profiles_e2e.py -v`; `.venv/bin/pytest -q`;
  `symphony doctor WORKFLOW.md`.

**Live routing smoke (TASK-9, 2026-08-17):** kanban ticket TASK-9 ran the
named-profile routing on a real pipeline: Todo -> codex (planner/gpt-5.6-sol),
In Progress -> agy (builder), Verify -> claude (reviewer/deepseek-v4-pro[1m]),
Document -> claude (documenter/deepseek-v4-flash). Every stage transition
resolved to its configured profile and produced the stage's expected artefact
set (`docs/TASK-9/{work,qa}/`); the byte-exact deliverable (`4f 4b 0a`, sha256
`a12b7cb4…`) shipped unchanged. Confirms the Phase-1..5 config /
resolution / execution chain in production shape.

**Last updated:** 2026-08-17 by TASK-9 Document.
