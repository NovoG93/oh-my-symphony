# TASK-15 Work Details: Usage Probes for AGY, Claude, OpenCode/Pi/Prime, Gemini, Kiro

## Architecture & Implementation Overview

This work completes Stages 2.2-2.8 of the Usage-Aware Agent Profiles specification, implementing usage probes, normalization, runtime exhaustion detection, and delegation across all remaining backends while rigorously enforcing the fail-open invariant.

### 1. AGY Backend (`src/symphony/backends/agy.py`) - Stage 2.2
- **`AgyUsageProbe`**: Implements `UsageProbe` using a read-only query command:
  `agy -p /quota --output-format json` (or configured query path).
  Fails open (`None`) if the command fails, errors, or times out.
- **`normalize_agy_usage`**: Preserves model and provider-specific quota bucket keys (e.g. `claude-3-5-sonnet`, `gemini-1.5-pro`) without assuming arbitrary positional hierarchies or fabricating synthetic window keys.
- **Registration**: Lazily registered as `USAGE_PROBES["agy"] = AgyUsageProbe`.

### 2. Claude Code Backend (`src/symphony/backends/claude_code.py`) - Stage 2.3
- **`ClaudeUsageProbe`**: Passive/cached adapter that reads telemetry recorded during CLI turns. Fails open (`None`) on cold start before any session turn has completed.
- **`normalize_claude_usage`**: Maps Claude duration windows:
  - `five_hour` -> `five_hour`
  - `seven_day` -> `weekly`
  Supports single-window telemetry or missing rate limits without fabricating missing windows.
- **Runtime Exhaustion**: `_is_genuine_claude_exhaustion` distinguishes subscription plan exhaustion (e.g. "usage limit reached", "quota exceeded", "credit balance is too low") from transient RPM/TPM rate limits. Emits `EVENT_PROVIDER_USAGE_EXHAUSTED` and raises `ProviderCapacityError`.

### 3. OpenCode Backend (`src/symphony/backends/opencode.py`) - Stage 2.4 & 2.6
- **Delegation**: Backend `kind` does not implicitly dictate `usage_pool`. Profiles bind an explicit `usage_pool` (e.g. `opencode-codex` binds `usage_pool: codex`).
- **`normalize_opencode_local_usage`**: Local token and cost estimates are marked `authoritative=False`. Non-authoritative snapshots never block scheduler dispatch under `ProviderUsageManager.evaluate()`.
- **`OpenCodeGoUsageProbe`**: Registered for `opencode` and `opencode-go` with fail-open behavior.
- **Runtime Exhaustion**: `_is_genuine_opencode_exhaustion` detects upstream quota exhaustion errors during per-turn CLI runs.

### 4. Pi & Prime Agent Backends (`src/symphony/backends/pi.py`) - Stage 2.4 & 2.7
- **Profile Binding**: Pi and Prime Agent profiles bind explicit usage pools (e.g. `pi-codex -> codex`, `pi-copilot -> github-copilot`). Omitted `usage_pool` falls back to the agent's self pool.
- **`GithubCopilotUsageProbe`**: Implements fail-open probe for GitHub Copilot (percentage unknown, hard limit detection only).
- **Runtime Exhaustion**: `_is_genuine_pi_exhaustion` catches provider exhaustion in `agent_end` stop reasons, stderr tails, and non-zero exit codes, emitting `EVENT_PROVIDER_USAGE_EXHAUSTED` and raising `ProviderCapacityError`.

### 5. Gemini Backend (`src/symphony/backends/gemini.py`) - Stage 2.5
- **Fail-Open Probe**: `GeminiUsageProbe` returns `None` (fails open without scraping pseudo-TTY `/stats`).
- **`normalize_gemini_usage`**: Normalizes structured quota schemas with ISO/epoch reset timestamps.
- **Reset Extraction & Exhaustion**: `_parse_gemini_exhaustion` parses runtime exhaustion errors ("quota exceeded", "resource has been exhausted") and extracts reset times from ISO timestamps ("resets at ..."), retry seconds ("retry after 60s"), or reset minutes ("resets in 5m"). Emits `EVENT_PROVIDER_USAGE_EXHAUSTED` and raises `ProviderCapacityError`.

### 6. Kiro Backend (`src/symphony/backends/kiro.py`) - Stage 2.8
- **Credit Normalization**: `normalize_kiro_usage` normalizes credit-based usage into a `monthly` window:
  `used_percent = (used_credits / total_credits) * 100.0`
  `remaining_percent = max(0.0, 100.0 - used_percent)`
- **`KiroUsageProbe`**: Fails open without interactive scraping.
- **Runtime Exhaustion**: `_is_genuine_kiro_exhaustion` detects credit exhaustion ("credits exhausted", "insufficient credits", "monthly limit reached") and triggers provider capacity waits.

### 7. Per-Turn Base Backend (`src/symphony/backends/per_turn.py`)
- Integrated `_usage_manager` and `_usage_pool` tracking into `PerTurnCliBackend`.
- Added hook `_check_provider_exhaustion(text)` and updated `_fail_turn` to emit `EVENT_PROVIDER_USAGE_EXHAUSTED` and raise `ProviderCapacityError` when genuine exhaustion is encountered.

### 8. Probe Registry (`src/symphony/backends/usage.py`)
- Updated `get_usage_probe(source)` to lazily resolve and cache probes for all supported sources:
  `codex`, `claude`, `agy`, `gemini`, `kiro`, `opencode-go`, `opencode`, and `github-copilot`.
