# docs/llm-wiki index

This directory (`docs/llm-wiki/`) is Symphony's domain knowledge base.
Each row points to a topic-scoped Markdown entry. Explore reads these
before any new ticket; Learn writes back to them after QA passes.

| topic-slug | summary | last touched |
|------------|---------|--------------|
| production-pipeline | Eight-stage pipeline + non-LLM Todo auto-triage + docs/<id>/<stage>/ artefact convention + WORKFLOW/PIPELINE sync invariant | 2026-05-17 (Todo auto-triage) |
| release-version-bump | Two-file lockstep version bump (`pyproject.toml:7` + `src/symphony/__init__.py:47`) + `chore(release)` hook contract + out-of-band tag rule | 2026-05-19 (REL-066) |
| orchestrator-phase-transition | `_rebuild_backend_for_phase` try/except envelope + `_install_fake_backend` factory pattern + WorkspaceManager hot-reload three-setter contract | 2026-05-17 (SMA-24) |
| worktree-git-sandbox | Linked-worktree git dir vs common dir split + the writable roots every backend must grant + host-owned Final History Gate + Blocked-history recovery | 2026-08-06 |
| workspace-auto-commit-excludes | Opt-in `symphony.autocommitExclude` git-config pathspec exclusions for final auto-commits, including base-squash safety and quoted pathspec handling | 2026-05-17 (SMA-25) |
| agent-observability | Headless event log signal set + cache token split + stall signatures + cross-refs to orchestrator/doctor/workspace | 2026-05-17 |
| board-viewer-theming | CSS variable token surface + `:root[data-theme="..."]` override pattern + UI Zoom micro-pattern mirrored for theme persistence. **Historical:** `tools/board-viewer/` was removed; the built-in web app is the only board | 2026-05-17 (SMA-23) |
| session-persistence | Per-workspace `.symphony-session.json` + load on dispatch + save on session_started + per-backend honor-points + codex `thread/resume` fallback | 2026-05-10 (SMA-20) |
| tui-rendering | Textual `KanbanApp` widget tree + diff-mount card refresh + heartbeat / observer / tracker poll cadence + invariants the helpers preserve | 2026-05-10 (Textual migration) |
| byte-exact-static-deliverables | printf-over-echo deterministic write + cmp/od/wc/sha256sum proof chain + workspace-fixture pattern when /tmp writes are blocked | 2026-08-15 (SMOKE-003) |
| agent-profile-config | Phase-1 named-profile model: AgentProfileConfig fields, PROFILE_FIELDS_BY_KIND allowlist, validation-at-build time, YAML duplicate-key boundary, no runtime consumers until Phase 2/3 | 2026-08-15 (TASK-4) |
| agent-profile-resolution | Phase-2 runtime resolution: AgentSelection + 8-tier precedence + resolve_agent_config non-null overlay + BackendInit selection/resolved_backend_config + per-transition F-01 re-resolution + ambiguity guard; stall-reconciler gap follow-up | 2026-08-15 (TASK-5) |
| agent-profile-backend-execution | Phase-3 backend execution: Claude `--model` injection after the `claude` token, dispatch `selection_for_state` + per-ticket ConfigValidationError refusal, profile-scoped session identity, 9 backend tests | 2026-08-15 (TASK-6) |
| agent-profile-observability-tooling | Phase-4 observability+tooling: run-record provenance (v9), ticket `agent.profile` override, `--agent-profile` CLI, doctor profile checks | 2026-08-15 (TASK-7) |
| agent-profiles-validation-and-docs | Phase-5 documentation & E2E validation: §20 acceptance config resolution, backward-compatibility regression, migration guidance, deferred UI boundary | 2026-08-16 (TASK-8) |

