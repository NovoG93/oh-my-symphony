# TASK-17: Stage 6 — Comprehensive Test Suite and Documentation Plan

## Goal
Complete Stage 6 (final stage) of the Usage-Aware Agent Profiles plan:
1. Deliver the comprehensive test suite across all 13 sub-areas (Stage 6.1 through 6.13).
2. Validate the permanent global fail-open invariant across all 8 backends (`codex`, `claude`, `agy`, `gemini`, `kiro`, `opencode`, `pi`, `prime-agent`).
3. Update documentation (`README.md`, `WORKFLOW.example.md`, `WORKFLOW.file.example.md`, `docs/features/agent-profiles.md`, `docs/llm-wiki/`) clarifying the profile-vs-pool boundary.

---

## Test Inventory & Coverage Map

| Spec Section | Target Test Module | Key Behaviors Verified | Status |
|---|---|---|---|
| **6.1 Configuration** | `tests/test_usage_limits.py`, `tests/test_workflow_agent_profiles.py` | `UsagePoolConfig`, shared pools across same kind, wrapper binding (`pi`, `opencode`, `prime-agent`), invalid cap percent rejection (`-1`, `0`, `101`, `"80"`), unknown pool rejection, backward-compatibility when omitted, daily/monthly/arbitrary windows | Passing |
| **6.2 Generic Pools** | `tests/test_usage_limits.py`, `tests/test_orchestrator_usage_limits.py` | Same pool blocks all consumers, independent pools do not cross-block, generic windows (`five_hour`, `weekly`, `daily`, `monthly`), non-authoritative estimates never block | Passing |
| **6.3 Codex Probe** | `tests/test_codex_usage.py` | Duration-based window normalization (300 -> 5h, 10080 -> weekly, other -> minutes), read normalization, multiple limits preserved, updated notifications, apiKey auth non-authoritative, genuine vs RPM exhaustion | Passing |
| **6.4 AGY Probe** | `tests/test_backend_usage_probes.py` | Read-only `/quota` command, verbatim model buckets preserved, non-dict/error fail-open | Passing |
| **6.5 Claude Probe** | `tests/test_backend_usage_probes.py` | Passive/cached adapter, 5h & weekly normalization, missing/unknown fail-open, hard limit detection, genuine exhaustion keywords | Passing |
| **6.6 Gemini Probe** | `tests/test_backend_usage_probes.py` | Missing programmatic quota fails open (no TTY scraping), reset time extraction from runtime errors, genuine exhaustion vs 429 RPM | Passing |
| **6.7 Kiro Probe** | `tests/test_backend_usage_probes.py` | Monthly credit window normalization, missing probe fails open, credit exhaustion detection | Passing |
| **6.8 OpenCode Probe** | `tests/test_backend_usage_probes.py` | Local stats `authoritative=False` (never blocks), delegation to bound pool (`opencode-codex -> codex`), runtime exhaustion detection | Passing |
| **6.9 Pi / Prime Probe** | `tests/test_backend_usage_probes.py` | Explicit pool binding required (kind does not imply pool), GithubCopilotUsageProbe fails open, Pi/Prime runtime exhaustion | Passing |
| **6.10 Scheduler** | `tests/test_orchestrator_usage_limits.py` | All same-pool profiles blocked at cap, other providers schedulable, exact-at-cap blocks, below-cap allows, reset restoration, stale refresh fail-open | Passing |
| **6.11 Worker Semantics**| `tests/test_orchestrator_usage_limits.py`, `tests/test_codex_usage.py` | Caps never interrupt running workers; genuine provider quota exhaustion (`EVENT_PROVIDER_USAGE_EXHAUSTED` / `ProviderCapacityError`) does not burn retry budget | Passing |
| **6.12 API/UI Contract** | `tests/test_webapi.py`, `tests/test_web_static_contract.py`, `tests/test_i18n.py` | `usage_pools` in workflow payload, `provider_usage` in orchestrator snapshot, `remaining_percent = 100 - used_percent`, Provider Usage card, EN/KO i18n | Passing |
| **6.13 Fail-Open Invariant**| `tests/test_backend_usage_probes.py`, `tests/test_orchestrator_usage_limits.py` | Parameterized test across all 8 backend kinds: probe exception/failure NEVER prevents dispatch | Passing |

---

## Documentation Updates

1. **`README.md`**: Added `Usage Pools & Quota Management (usage_pools)` subsection explaining the core boundary: profiles define HOW an agent runs, pools define WHETHER work may start.
2. **`WORKFLOW.example.md` & `WORKFLOW.file.example.md`**: Documented `usage_pools:` example block and `usage_pool:` references in `agent_profiles:`.
3. **`docs/features/agent-profiles.md`**: Documented Stage 6 test suite coverage and permanent global fail-open invariant.
4. **`docs/llm-wiki/usage-aware-agent-profiles.md`**: Updated with Stage 6 summary, decision log, and test evidence.
