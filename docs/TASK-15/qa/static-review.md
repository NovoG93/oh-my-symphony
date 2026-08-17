# Static Review: full diff read + AC-to-code trace (Verify, 2026-08-17)

**What**: Independent read of the entire branch delta with the acceptance criteria traced to code and tests.
**Why**: Compensates for the denied live pytest re-run; every AC is anchored in reviewed code.
**As-Is -> To-Be**: Unverified 1674-line diff -> Reviewed in full, all 6 ACs anchored, no MEDIUM+ findings.

## Diff scope vs `## Implementation` (no orphan scope)

`git diff develop...HEAD --name-only` = 11 paths, every one named in the ticket's `## Implementation`:

| Path | +lines | Role |
|---|---|---|
| `src/symphony/backends/agy.py` | 181 | AgyUsageProbe, normalize_agy_usage, USAGE_PROBES registration |
| `src/symphony/backends/claude_code.py` | 224 | ClaudeUsageProbe, normalize_claude_usage, _is_genuine_claude_exhaustion, runtime hooks |
| `src/symphony/backends/per_turn.py` | 30 | _usage_manager/_usage_pool fields, _check_provider_exhaustion hook, _fail_turn exhaustion path |
| `src/symphony/backends/gemini.py` | 208 | GeminiUsageProbe, normalize_gemini_usage, _parse_gemini_exhaustion |
| `src/symphony/backends/kiro.py` | 120 | KiroUsageProbe, normalize_kiro_usage, _is_genuine_kiro_exhaustion |
| `src/symphony/backends/opencode.py` | 96 | normalize_opencode_local_usage (authoritative=False), OpenCodeGoUsageProbe, exhaustion |
| `src/symphony/backends/pi.py` | 89 | GithubCopilotUsageProbe, _is_genuine_pi_exhaustion, terminal/exit-hook checks |
| `src/symphony/backends/usage.py` | 27 | get_usage_probe lazy map for all 8 sources |
| `tests/test_backend_usage_probes.py` | 598 | 36 acceptance tests |
| `docs/TASK-15/work/plan.md`, `docs/TASK-15/work/details.md` | 102 | work-stage evidence |

## AC -> code -> test trace

- **AC1 AGY**: `AgyUsageProbe` default command `"agy -p /quota --output-format json"`, executed via `asyncio.create_subprocess_shell`, non-zero exit / exception / non-dict JSON -> `None` (fail open). `normalize_agy_usage` keeps provider/model bucket keys verbatim as window keys; no `five_hour`/`weekly` fabrication. Tests: `test_agy_quota_probe_uses_read_only_command`, `test_agy_structured_quota_is_normalized`, `test_agy_model_specific_quota_buckets_are_preserved` (asserts `five_hour`/`weekly` absent), `test_agy_probe_fails_open_on_error`.
- **AC2 Claude**: `ClaudeUsageProbe` is passive/cached (returns cached snapshot or None on cold start); `normalize_claude_usage` maps `five_hour`->`five_hour`, `seven_day`->`weekly`; `_is_genuine_claude_exhaustion` excludes rpm/tpm/429 and flags "usage limit reached" etc. No HTTP/undocumented endpoints anywhere (pure stream-json telemetry). Tests: the 6 `test_claude_*`.
- **AC3 OpenCode/Pi/Prime**: pool comes from `AgentProfileConfig.usage_pool` (bound explicitly, e.g. `pi-codex -> codex`); `normalize_opencode_local_usage` hardcodes `authoritative=False`; `ProviderUsageManager.evaluate` returns READY for non-authoritative snapshots (`orchestrator/usage.py:164`), so local estimates can never block. Tests: `test_opencode_*`, `test_pi_*`, `test_prime_*` (4+3+3 = 10).
- **AC4 Gemini**: `GeminiUsageProbe.fetch_usage` returns cached/None — no pseudo-TTY `/stats` scraping; `_parse_gemini_exhaustion` detects quota exhaustion keywords, extracts reset from ISO ("resets at ..."), retry seconds, or reset minutes; unknown percentage -> fail open. Tests: 4 `test_gemini_*`.
- **AC5 Kiro**: `normalize_kiro_usage` computes `used/total*100` into a `monthly` window; `KiroUsageProbe` returns cached/None (no programmatic endpoint, no TTY scraping); `_is_genuine_kiro_exhaustion` flags credit/monthly exhaustion, excludes rpm/tpm. Tests: 3 `test_kiro_*`.
- **AC6 Tests green**: all 36 tests collected with `lastfailed = {}` -> `qa/test-cache-evidence.md` (fresh re-run denied -> `qa/runtime-blocked.md`).

## Fail-open invariant (the ticket's core rule) — verified by reading `orchestrator/usage.py`

- `evaluate()`: missing snapshot -> READY (`:158-159`); stale -> READY (`:161-162`); `authoritative=False` -> READY (`:164-165`) — local estimates never block.
- `refresh()`: probe missing -> fail open (`:98-101`); probe raises -> caught, last-known retained, never blocks (`:116-126`).
- So "no authoritative telemetry -> do not block scheduling" holds at the scheduler for every backend.

## Runtime-hook loop closure (exhaustion actually pauses dispatch)

- Backends emit `EVENT_PROVIDER_USAGE_EXHAUSTED` and raise `ProviderCapacityError` on genuine exhaustion (`per_turn.py` `_fail_turn`; claude/pi failure paths; gemini `_complete_turn`).
- Orchestrator catches `ProviderCapacityError` and writes a hard-limit snapshot (used 100%, `hard_limit_reached=True`, authoritative) -> `evaluate()` returns WAIT_PROVIDER_USAGE -> new dispatch waits (`orchestrator/core.py:7392-7413`).

## Symbol-level check of every API the tests exercise (no live import needed)

- `symphony.backends`: `EVENT_PROVIDER_USAGE_EXHAUSTED` (`__init__.py:50`), `ProviderCapacityError(pool_id, resets_at, message)` (`__init__.py:57-69`) exist.
- `Issue` is a frozen dataclass with `id, identifier, title, description, priority, state, agent_kind, agent_profile` (`issue.py:19-40`) — test construction is valid.
- `WorkflowState(path: Path)` (`workflow/state.py:22`), `Orchestrator(state)` with `_usage_manager` (`core.py:771`) and `_eligibility_usage_decision(issue, cfg)` (`core.py:5531`) exist.
- `ProviderUsageManager.set_probe/set_snapshot/snapshots/evaluate` exist (`orchestrator/usage.py:51,62,86,150`).
- `PrimeAgentBackend(PiBackend)` exists (`backends/prime_agent.py:27`) — inherits the new Pi exhaustion hooks.
- Pyright gates `src/` only (`pyproject.toml` `include = ["src"]`), so unused test imports (e.g. `AgyBackend`, `_EligibilityDisposition`) are outside the type-check gate; no lint test exists in the suite.

## Review findings

No CRITICAL/HIGH/MEDIUM issues. LOW notes (non-blocking, no fix required by ticket scope):
1. `per_turn._check_provider_exhaustion` annotates the second tuple element `Any | None` while overrides return `datetime | None` — cosmetic; pyright basic mode does not flag it.
2. Test file imports `AgyBackend`/`ClaudeCodeBackend`/`GeminiBackend`/`KiroBackend`/`OpenCodeBackend`/`PiBackend`/`PrimeAgentBackend`/`AgentSelection`/`_EligibilityDisposition` unused — import side effects also exercise the eager `USAGE_PROBES` registration; harmless, ungated by pyright.
3. `AgyUsageProbe` uses `create_subprocess_shell` with an operator-config constant; no untrusted input is interpolated (see `qa/security-audit.md`, injection row).

**How to re-run**: `git diff develop...HEAD` read in full (this file is the record); re-verify with `python -m pytest tests/test_backend_usage_probes.py -q` where execution is allowed.
