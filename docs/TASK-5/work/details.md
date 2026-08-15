# TASK-5: Named Agent Profiles — Phase 2 Work Notes

## Objective
Implement runtime resolution for named agent profiles:
- `AgentSelection` dataclass (`kind`, `profile`).
- `selection_for_state` resolver implementing the 8-tier precedence hierarchy.
- `resolve_agent_config` overlaying profile parameters on top of global backend config with immutable dataclass replacement.
- `BackendInit` extended with `selection` and `resolved_backend_config`.
- Stage transition lifecycle ensuring profile is re-evaluated on every stage transition.
- Ambiguous ticket overrides (setting both `agent_kind` and `agent_profile`) rejected.

## Design Decisions
1. **Precedence Hierarchy**:
   1. explicit dispatch profile
   2. explicit dispatch kind
   3. ticket agent_profile
   4. ticket agent_kind
   5. agent.stage_profiles[state]
   6. agent.stage_kinds[state]
   7. agent.default_profile
   8. agent.kind
2. **Ambiguity Guard**:
   If a ticket specifies both `agent_kind` and `agent_profile`, `ConfigValidationError` is raised immediately.
3. **BackendInit Backwards Compatibility**:
   `selection` and `resolved_backend_config` default to `None` in `BackendInit`'s signature, and `__post_init__` defaults them from `cfg` and `resolve_agent_config`, ensuring all existing tests and callers continue working without modification.
4. **Lifecycle Re-Resolution**:
   `_rebuild_backend_for_phase` re-evaluates `selection_for_state` against `base_cfg` for the new phase's target state, ensuring stage transitions pick up stage profile changes.
