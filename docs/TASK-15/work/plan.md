# TASK-15 Work Plan: Stage 2.2-2.8 Backend Usage Probes

**What**: Implement usage probes for AGY, Claude Code, Gemini, Kiro, OpenCode, Pi, and Prime Agent backends while preserving the fail-open invariant.
**Why**: Ensures multi-agent workflows can observe quota consumption across diverse provider backends without risking stalled scheduling when telemetry is unavailable.
**As-Is -> To-Be**:
- As-Is: Only the Codex backend has an authoritative probe registered; other backends lack probe classes, normalization logic, and runtime exhaustion handling.
- To-Be: All 8 backends have dedicated or delegating probe classes, normalization helpers, exhaustion detection, and registered probe factories honoring the fail-open invariant.

## Target Architecture

1. **AGY (`src/symphony/backends/agy.py`)**:
   - `AgyUsageProbe`: Executes `agy -p /quota --output-format json` (read-only) and fails open on any error.
   - `normalize_agy_usage`: Preserves model/bucket-specific quota structures without forcing into 5h/weekly.
   - Dual registration in `USAGE_PROBES["agy"]` and `get_usage_probe("agy")`.

2. **Claude Code (`src/symphony/backends/claude_code.py`)**:
   - `ClaudeUsageProbe`: Passive/cached adapter; fails open on cold start / absence of telemetry.
   - `normalize_claude_usage`: Maps `five_hour` -> `five_hour` and `seven_day` -> `weekly`; flags hard limit on usage limit errors.
   - Runtime exhaustion detection for `ProviderCapacityError` / `EVENT_PROVIDER_USAGE_EXHAUSTED`.
   - Dual registration in `USAGE_PROBES["claude"]` and `get_usage_probe("claude")`.

3. **Gemini (`src/symphony/backends/gemini.py`)**:
   - `GeminiUsageProbe`: Returns `None` on fetch (fails open; no pseudo-TTY scraping of `/stats`).
   - `normalize_gemini_usage`: Generic quota snapshot normalization.
   - Runtime error parsing for quota exhaustion + best-effort reset extraction (`ProviderCapacityError`).
   - Dual registration in `USAGE_PROBES["gemini"]` and `get_usage_probe("gemini")`.

4. **Kiro (`src/symphony/backends/kiro.py`)**:
   - `KiroUsageProbe`: Returns `None` on fetch (fails open; no interactive scraping).
   - `normalize_kiro_usage`: Normalizes `(used_credits / total_credits) * 100` into `monthly` window.
   - Runtime error parsing for credit exhaustion (`ProviderCapacityError`).
   - Dual registration in `USAGE_PROBES["kiro"]` and `get_usage_probe("kiro")`.

5. **OpenCode (`src/symphony/backends/opencode.py`)**:
   - Provider delegation: `kind: opencode` does not imply `usage_pool: opencode`. Profiles bind explicit pool (e.g. `codex`).
   - `normalize_opencode_local_usage`: Marks local estimates with `authoritative=False` (never blocks scheduling).
   - `OpenCodeGoUsageProbe`: Returns non-authoritative snapshot or `None`.
   - Runtime error parsing for exhaustion (`ProviderCapacityError`).
   - Dual registration in `USAGE_PROBES["opencode-go"]` and `USAGE_PROBES["opencode"]`.

6. **Pi (`src/symphony/backends/pi.py`) & Prime Agent (`src/symphony/backends/prime_agent.py`)**:
   - Explicit pool delegation (e.g. `pi-codex` -> `codex`, `pi-copilot` -> `github-copilot`).
   - `GithubCopilotUsageProbe`: Returns `None` (fail open), hard-limit detection only.
   - Runtime exhaustion detection (`ProviderCapacityError`).
   - Dual registration in `USAGE_PROBES["github-copilot"]`.

7. **Probe Registry (`src/symphony/backends/usage.py`)**:
   - `get_usage_probe(source)` with lazy import map for all sources.

8. **Test Suite (`tests/test_backend_usage_probes.py`)**:
   - Comprehensive test suite covering Stage 6.4 - 6.9 and Stage 6.13.
