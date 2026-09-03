# TASK-18 Work Notes: Copilot Backend Phase 1

## Overview
Phase 1 extracts GitHub Copilot into its own backend module (`src/symphony/backends/copilot.py`) and wires the `copilot` kind throughout Symphony's configuration, profiles, factory, and doctor systems.

## Architectural Changes
1. **Module Creation**: `src/symphony/backends/copilot.py`
   - `CopilotBackend(PerTurnCliBackend)`
   - `CopilotUsageProbe(UsageProbe)`
   - JSONL event parser and session manager
   - Genuine quota exhaustion detection
   - Probe registry hook `USAGE_PROBES["copilot"] = CopilotUsageProbe`
2. **Pi Backend Cleanup**: `src/symphony/backends/pi.py`
   - Removed `GithubCopilotUsageProbe` and registration
   - Removed unused usage imports
   - Verified zero Copilot symbols remain in `pi.py`
3. **Usage Registry Canonicalization**: `src/symphony/backends/usage.py`
   - Canonicalized `copilot` source
   - Added `USAGE_SOURCE_ALIASES = {"github-copilot": "copilot"}`
   - Updated `get_usage_probe` lazy resolution
4. **Constants**: `src/symphony/workflow/constants.py`
   - Added `"copilot"` to `SUPPORTED_AGENT_KINDS`
   - Added `"copilot"` profile field allowlist (pi fields + `model`, `reasoning_effort`)
   - Added `DEFAULT_COPILOT_COMMAND = "copilot"`
5. **Config Model**: `src/symphony/workflow/config.py`
   - Added `CopilotConfig` dataclass
   - Added `_default_copilot_config()` helper
   - Added defaulted `copilot: CopilotConfig | None = None` on `ServiceConfig`
   - Updated `ServiceConfig.backend_timeouts()` for `copilot`
6. **Config Builder**: `src/symphony/workflow/builder.py`
   - Parse `copilot:` configuration block into `CopilotConfig`
7. **Profiles**: `src/symphony/workflow/profiles.py`
   - Added `copilot: CopilotConfig | None = None` on `ResolvedAgentConfig`
   - Updated `_get_backend_config` for `copilot`
8. **Factory**: `src/symphony/backends/__init__.py`
   - Added `copilot` branch to `build_backend()`
   - Updated unsupported kind exception message
9. **Preflight**: `src/symphony/workflow/preflight.py`
   - Validated `copilot.command` non-empty when configured
10. **Doctor**: `src/symphony/cli/doctor.py`
    - Added `check_copilot_auth()`
    - Updated CLI command detection and check list
