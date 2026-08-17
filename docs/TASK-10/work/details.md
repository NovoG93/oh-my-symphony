# TASK-10 Work Details & Implementation Notes

## Goal & Scope
Make per-stage agent profile resolution observable across dispatches and stage transitions:
1. Log resolved `agent_profile`, `model`, and `reasoning_effort` alongside `agent_kind` in the `dispatch` log ([core.py](file:///home/symphony/symphony_workspaces/TASK-10/src/symphony/orchestrator/core.py#L6431-L6440)).
2. Fire `stage_backend_rerouted` when either backend kind or profile/model/reasoning_effort changes across stage transitions ([core.py](file:///home/symphony/symphony_workspaces/TASK-10/src/symphony/orchestrator/core.py#L7039-L7096)).
3. Emit rich transition metadata: `from_profile`, `to_profile`, `from_model`, `to_model`, `to_reasoning_effort`, `from_kind`, `to_kind`.
4. Persist the current stage's resolved `agent_profile`, `model`, and `reasoning_effort` to the `runs` table in `.symphony/state.db` on each stage transition via [`RunRegistry.update_stage_agent_profile`](file:///home/symphony/symphony_workspaces/TASK-10/src/symphony/orchestrator/run_registry.py#L605-L644).
5. Maintain strict backward compatibility for legacy workflows without profiles.

## Implementation Details
1. **`RunRegistry.update_stage_agent_profile`**:
   - Added method in [`run_registry.py`](file:///home/symphony/symphony_workspaces/TASK-10/src/symphony/orchestrator/run_registry.py#L605-L644).
   - Updates `state`, `agent_kind`, `agent_profile`, `model`, `reasoning_effort`, `updated_at` on active owned run row in `.symphony/state.db`.
2. **`core.py` Orchestrator Enhancements**:
   - Enhanced `dispatch` log at line ~6437 to include `agent_profile`, `model`, `reasoning_effort`.
   - Enhanced lease reacquire at line ~2434 to pass `agent_profile`, `model`, `reasoning_effort`.
   - In stage transition handling (lines ~7039-7118): resolved `to_profile`, `to_model`, `to_reasoning_effort` using `phase_cfg.selection_for_state` and `resolve_agent_config`. Logged `stage_backend_rerouted` if any field differs. Updated `running_entry` in memory and persisted to DB via `update_stage_agent_profile`.
3. **Verification**:
   - Re-run command: `./.venv/bin/pytest tests/test_workflow_agent_profiles_runtime.py tests/test_run_registry.py`
   - All tests passing with 0 regressions.

