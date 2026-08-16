# TASK-7 Work Notes: Observability + Tooling for Named Agent Profiles

## Objectives
Implement Phase 4 of Named Agent Profiles (observability and tooling):
1. **Run records persistence**: Add `agent_profile`, `model`, and `reasoning_effort` columns to the `runs` SQLite table via a transactional migration (v9), update `RunRecord` dataclass, `RunRegistry.acquire_run`, `acquire_continuation_run`, `_record`, `_run_summary`, and orchestrator `RunningEntry` + `_run_record_payload`.
2. **Ticket-level profile override**: Support `agent: {profile: ...}` and `agent_profile:` in ticket frontmatter, reject ambiguous configurations (both `agent_kind` and `agent_profile` specified), and ensure `FileBoardTracker.create` and `update_fields` write and update `agent: {profile: ...}`.
3. **CLI tooling**: Support `--agent-profile` on `symphony board new` and `symphony board update`, validate profile existence against `WORKFLOW.md`, reject ambiguous `--agent-kind` and `--agent-profile` flags, and display profile in `symphony board show`.
4. **Symphony doctor**: Validate profile configurations, supported backend kinds, executable existence on PATH, model syntax, stage_profiles resolution, and default_profile resolution, emitting PASS/FAIL/WARN rows.

## Invariants & Design Decisions
- Schema migration v9 is additive and idempotent using `_add_column_if_missing`.
- Ambiguous overrides (`agent_kind` + `agent_profile`) are rejected in both ticket frontmatter parsing and CLI argument handling.
- `symphony doctor` produces PASS for valid profiles, WARN when a profile overrides the global command executable, and FAIL when an executable is missing on `$PATH` or model syntax / stage reference is invalid.
- No web UI changes in this phase.
