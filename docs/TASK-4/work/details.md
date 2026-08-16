# TASK-4 Work Details: Named Agent Profiles Phase 1

## Overview
Phase 1 implements the configuration model for named agent profiles:
- `AgentProfileConfig` dataclass in `src/symphony/workflow/config.py`
- Allowlist mapping `PROFILE_FIELDS_BY_KIND` in `src/symphony/workflow/constants.py`
- Configuration parsing and validation helpers in `src/symphony/workflow/builder.py`:
  - `_validated_agent_profiles`
  - `_validated_stage_profiles`
  - `_validated_default_profile`
- Projection into `AgentConfig` and `ServiceConfig`
- Unit tests covering positive parsing and all rejection scenarios in `tests/test_workflow_agent_profiles.py`

## Validation Rules Enforced
1. **Profile Names**: Must be non-empty strings and unique (duplicate names or whitespace-normalizing collisions raise `ConfigValidationError`).
2. **Backend Kinds**: Must be in `SUPPORTED_AGENT_KINDS` (with `antigravity` canonicalized to `agy`).
3. **Field Allowlists**: Per-kind fields validated against `PROFILE_FIELDS_BY_KIND` (rejecting unsupported fields such as `reasoning_effort` on `agy` or `claude`, `resume_across_turns` on `codex`).
4. **Field Types**:
   - `model`: string
   - `reasoning_effort`: string
   - `command`: string
   - `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms`: positive integers
   - `resume_across_turns`: boolean
5. **Stage Profiles**: Mapping of tracker states to profile names; state names normalized, profile names must exist in `agent_profiles`.
6. **Default Profile**: Must reference an existing profile in `agent_profiles`.

## Parser & Builder Boundaries
- **Duplicate YAML Keys**: Standard `yaml.safe_load` (PyYAML) collapses exact duplicate mapping keys at parse time before reaching the builder. The builder enforces uniqueness for distinct mapping entries normalizing to the same trimmed name (`" qa"` vs `"qa"`). Rejecting exact duplicate keys in YAML source text would require a custom duplicate-key YAML loader (similar to `src/symphony/orchestrator/release_contracts.py:109`), which is outside Phase 1 config model scope.

## Verification Commands
- Unit tests: `pytest tests/test_workflow_agent_profiles.py` (16 passed)

