# TASK-4 Named Agent Profiles Phase 1 Work Notes

## Objective
Implement Phase 1 configuration model for named agent profiles:
- `AgentProfileConfig` frozen dataclass
- Parsing of top-level `agent_profiles:` map and `agent.stage_profiles:`, `agent.default_profile:`
- Validation with `PROFILE_FIELDS_BY_KIND` backend allowlist and early failure on malformed config
- Backward compatibility with existing workflows

## Model Specifications
### AgentProfileConfig
Defined in `src/symphony/workflow/config.py`:
- `name: str` (required)
- `kind: str` (required)
- `model: str | None = None`
- `reasoning_effort: str | None = None`
- `command: str | None = None`
- `turn_timeout_ms: int | None = None`
- `read_timeout_ms: int | None = None`
- `stall_timeout_ms: int | None = None`
- `resume_across_turns: bool | None = None`

### PROFILE_FIELDS_BY_KIND Allowlist
Defined in `src/symphony/workflow/constants.py`:
- `codex`: `{"model", "reasoning_effort", "command", "turn_timeout_ms", "read_timeout_ms", "stall_timeout_ms"}`
- `claude`: `{"model", "command", "resume_across_turns", "turn_timeout_ms", "read_timeout_ms", "stall_timeout_ms"}`
- `gemini`: `{"command", "resume_across_turns", "turn_timeout_ms", "read_timeout_ms", "stall_timeout_ms"}`
- `agy`: `{"command", "resume_across_turns", "turn_timeout_ms", "read_timeout_ms", "stall_timeout_ms"}`
- `kiro`: `{"command", "resume_across_turns", "turn_timeout_ms", "read_timeout_ms", "stall_timeout_ms"}`
- `opencode`: `{"command", "resume_across_turns", "turn_timeout_ms", "read_timeout_ms", "stall_timeout_ms"}`
- `pi`: `{"command", "resume_across_turns", "turn_timeout_ms", "read_timeout_ms", "stall_timeout_ms"}`
- `prime-agent`: `{"command", "resume_across_turns", "turn_timeout_ms", "read_timeout_ms", "stall_timeout_ms"}`

### Validation Helpers in builder.py
1. `_validated_agent_profiles(raw: Any) -> dict[str, AgentProfileConfig]`:
   - Checks mapping type.
   - Validates profile names (non-empty strings).
   - Validates `kind` (required, non-empty string, in `SUPPORTED_AGENT_KINDS`, alias `antigravity` canonicalized to `agy`).
   - Validates field allowlist via `PROFILE_FIELDS_BY_KIND`.
   - Validates field types (`model`, `reasoning_effort`, `command` as string; timeouts as positive ints; `resume_across_turns` as bool).
2. `_validated_stage_profiles(value: Any, *, agent_profiles: dict[str, AgentProfileConfig], active_states: tuple[str, ...], terminal_states: tuple[str, ...]) -> dict[str, str]`:
   - Validates dictionary mapping.
   - Normalizes state keys.
   - Checks that referenced profile names exist in `agent_profiles`.
3. `_validated_default_profile(value: Any, *, agent_profiles: dict[str, AgentProfileConfig]) -> str | None`:
   - Validates string type.
   - Checks that referenced profile exists in `agent_profiles`.
