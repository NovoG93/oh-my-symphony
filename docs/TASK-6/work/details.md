# TASK-6 Work Details

## Implementation Details

### Claude Model Injection
- File: `src/symphony/backends/claude_code.py`
- Added helper function:
```python
def _inject_model(command: str, model: str) -> str:
    """Inject ``--model <model>`` right after a literal ``claude`` token."""
    if not model:
        return command
    stripped = command.lstrip()
    if not (stripped == "claude" or stripped.startswith(("claude ", "claude\t"))):
        return command
    leading_ws = command[: len(command) - len(stripped)]
    rest = stripped[len("claude") :]
    flag = f"--model {shlex.quote(model)}"
    return f"{leading_ws}claude {flag}{rest}"
```
- In `ClaudeCodeBackend.run_turn()`:
```python
cmd = _inject_model(self._claude.command, self._claude.model)
cmd = _inject_add_dirs(cmd, self._git_roots)
```

### Codex Model & Reasoning Effort
- File: `src/symphony/backends/codex.py`
- `CodexAppServerBackend` receives resolved `CodexConfig` via `BackendInit.resolved_backend_config`.
- `_build_turn_params()` sets `params["model"] = self._codex.model` and `params["effort"] = self._codex.reasoning_effort`.
- `_prepare_command_and_env()` runs the resolved `self._codex.command`.

### Dispatch Selection Alignment & Robustness
- File: `src/symphony/orchestrator/core.py`
- In `_dispatch`:
```python
try:
    dispatch_selection = cfg.selection_for_state(
        issue.state,
        ticket_profile=_requested_agent_profile(issue),
        ticket_kind=_requested_agent_kind(issue),
    )
except ConfigValidationError as exc:
    log.error(
        "dispatch_selection_refused",
        issue_id=issue.id,
        identifier=issue.identifier,
        error=str(exc),
    )
    return False
agent_kind = dispatch_selection.kind
```
- Wrapped `cfg.selection_for_state` in `try/except ConfigValidationError` so ambiguous overrides (both `agent_kind` + `agent_profile`) or unknown profiles cleanly refuse dispatch for that individual ticket rather than crashing the tick scheduler loop.
- Added regression test `test_dispatch_refuses_ambiguous_and_unknown_agent_profile_without_raising` in `tests/test_workflow_agent_profiles_backend.py`.

