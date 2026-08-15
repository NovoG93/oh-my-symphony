# Named agent profiles — Phase 1 configuration model

**Summary:** TASK-4 delivered the Phase-1 configuration model for named
agent profiles: a frozen `AgentProfileConfig` dataclass, parsing of
`agent_profiles:` / `agent.stage_profiles:` / `agent.default_profile:`
from WORKFLOW.md, and validation that fails at config-build time
(`ConfigValidationError`), not at dispatch. No runtime dispatch or backend
selection consumes these fields yet — that is Phase 2/3 work.

**Model & validation invariants:**
- `AgentProfileConfig` (`src/symphony/workflow/config.py`): `name` and
  `kind` required; `model`, `reasoning_effort`, `command`,
  `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms`,
  `resume_across_turns` optional — `None` means inherit from the global
  backend config.
- `PROFILE_FIELDS_BY_KIND` (`src/symphony/workflow/constants.py`) is the
  per-kind field allowlist: `codex` allows `model` + `reasoning_effort`;
  `claude` allows `model` but NOT `reasoning_effort`; `agy`, `gemini`,
  `kiro`, `opencode`, `pi`, `prime-agent` allow neither. `command` and the
  three timeouts are allowed everywhere; `resume_across_turns` everywhere
  except `codex`. Any other field raises `ConfigValidationError`
  (e.g. `reasoning_effort` on `agy`).
- `_validated_agent_profiles` / `_validated_stage_profiles` /
  `_validated_default_profile` (`src/symphony/workflow/builder.py`) run
  inside `build_service_config`, so malformed profile config fails during
  workflow/config validation — never later at dispatch.
- Type rules: `model`/`reasoning_effort`/`command` must be strings;
  timeouts must be positive ints (bool explicitly rejected);
  `resume_across_turns` must be a bool. Profile names are stripped,
  non-empty, and unique — whitespace-colliding names (`" qa"` vs `"qa"`)
  raise. `antigravity` canonicalizes to `agy`.
- `agent.stage_profiles` maps tracker states to profile names (keys
  lowercased); referenced profiles must exist; unknown state keys log a
  warning but do not fail, staying forward-compatible with board lane
  renames. `agent.default_profile` must reference an existing profile.
- Duplicate-key boundary: YAML-level exact duplicate keys are collapsed
  by PyYAML (`yaml.safe_load`) before the builder sees them; rejecting
  them needs a custom loader (pattern in
  `src/symphony/orchestrator/release_contracts.py`) and is out of
  Phase-1 scope.

**Phase boundary check:** grep that only `workflow/config.py`,
`workflow/builder.py`, `workflow/constants.py`, `workflow/__init__.py`
read `agent_profiles|stage_profiles|default_profile` — any dispatch or
backend consumer means Phase 2/3 leaked into this phase. **Update
(2026-08-15, TASK-5):** Phase 2 runtime consumers now exist by design —
see [[agent-profile-resolution]] for the resolver, overlay, and lifecycle
wiring.

**Evidence:** 16 unit tests in `tests/test_workflow_agent_profiles.py`;
TASK-4 QA artefacts under `docs/TASK-4/qa/` (ac-scorecard, qa-evidence,
security-audit, merge-preflight).

**Decision log:**
- 2026-08-15 | TASK-4 | Phase-1 config model only: dataclass + parsing +
  validation + tests. Runtime dispatch/backend selection deliberately
  untouched (Phase 2/3). Unknown `stage_profiles` state keys warn rather
  than fail.
- 2026-08-15 | TASK-4 | Duplicate profile names rejected at builder level
  after strip; PyYAML exact-duplicate collapse documented as a parser
  boundary, not a builder bug.

**Last updated:** 2026-08-15 by TASK-4 Document.
