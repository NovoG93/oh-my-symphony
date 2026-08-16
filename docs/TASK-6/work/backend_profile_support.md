# TASK-6: Named Agent Profiles — Phase 3 Backend Support

## Objective
Implement Phase 3 backend execution support for named agent profiles:
- Wire resolved `ClaudeConfig` and `CodexConfig` into their respective backend implementations.
- Inject `--model <model>` into Claude CLI commands when `ClaudeConfig.model` is set.
- Ensure profile overrides (`model`, `reasoning_effort`, `command`, timeouts, `resume_across_turns`) take effect during backend execution while un-overridden properties inherit cleanly from base config.
- Enforce session scoping by ticket + backend kind + profile so that distinct models/profiles using the same backend kind maintain separate sessions and never resume each other's sessions.

## Key Changes
1. **Claude CLI model injection (`src/symphony/backends/claude_code.py`)**:
   - Implemented `_inject_model(command: str, model: str) -> str` to inject `--model <model>` immediately following the `claude` command token, preserving whitespace, flags, and shell redirections/pipelines.
   - Wrapper scripts without a leading `claude` token are left intact.
   - Integrated `_inject_model` into `ClaudeCodeBackend.run_turn`.

2. **Codex CLI model and reasoning parameters (`src/symphony/backends/codex.py`)**:
   - Verified that `CodexAppServerBackend` receives resolved `CodexConfig` via `BackendInit.resolved_backend_config`.
   - `_build_turn_params` includes `params["model"] = self._codex.model` and `params["effort"] = self._codex.reasoning_effort` for all turn dispatches.
   - Custom `command` overrides are respected in `_prepare_command_and_env`.

3. **Session scoping, isolation & dispatch robustness (`src/symphony/orchestrator/core.py`)**:
   - `_dispatch` resolves `AgentSelection` via `selection_for_state` to ensure dispatch registration matches the resolved profile kind.
   - Wrapped `selection_for_state` in `try/except ConfigValidationError` to log per-ticket refusal and return `False` without crashing the scheduler tick loop when invalid or ambiguous overrides occur.
   - Stage transitions tear down existing backend instances and clear session/thread state (`running_entry.session_id = None`, `running_entry.thread_id = None`, `running_entry.resume_session_id = None`).
   - When a new stage begins with a different profile (even on the same backend kind, e.g. Codex/Sol -> Codex/Luna), a fresh backend instance is initialized with its own session.

4. **Testing Suite (`tests/test_workflow_agent_profiles_backend.py`)**:
   - Codex model & reasoning effort in `turn/start`.
   - Command inheritance and override for both Codex and Claude.
   - `--model` injection edge cases for Claude (quoting, pipelines, wrapper scripts).
   - `resume_across_turns` and timeout inheritance.
   - Cross-profile session isolation on same backend kind.
   - Dispatch refusal on ambiguous (`agent_kind` + `agent_profile`) and unknown profile configurations without raising exceptions.

