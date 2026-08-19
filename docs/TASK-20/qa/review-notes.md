# TASK-20 Verify — Review notes (diff vs plan §16–18, §26–27)

**What**: Full-diff review of `3a436bb..HEAD` (82ff203) against the plan, ticket ACs, and Done Signals.
**Why**: Prove the branch implements exactly Phase 3 with no orphan scope before merge.
**As-Is -> To-Be**: Unreviewed implementation -> clean review, zero CRITICAL/HIGH/MEDIUM findings.

## Conformance map

| Plan requirement | Implementation | Verdict |
|---|---|---|
| §16 `CopilotUsageProbe` lives in `copilot.py` | `CopilotUsageProbe(UsageProbe)` at `src/symphony/backends/copilot.py:423`, registered `USAGE_PROBES["copilot"]` | ✓ |
| §16 Stage A: runtime hard-limit detection via `CopilotBackend._check_provider_exhaustion()` | hook at copilot.py:67-70 delegates to `_is_genuine_copilot_exhaustion`; `_complete_turn` emits `EVENT_PROVIDER_USAGE_EXHAUSTED` + raises `ProviderCapacityError` (copilot.py:198-205) | ✓ |
| §16 generic 429/rate-limit NOT exhaustion | RPM/TPM guard short-circuits; `429 Too Many Requests`/`rate limit exceeded` assert False in `test_generic_rate_limit_does_not_mark_plan_exhausted` | ✓ |
| §17 spawn `copilot --server --stdio --no-auto-update --log-level error` | default `command=` in `CopilotUsageProbe.__init__` (copilot.py:429); `--server` guard appends flags if missing | ✓ |
| §17 LSP-framed JSON-RPC, `account.getQuota`, `{"params":{}}`, no initialize | request built at copilot.py:471-480; `_read_lsp_message` parses `Content-Length` header + body (copilot.py:513-553) | ✓ |
| §17 normalize `premium_interactions.remainingPercentage` → `used = 100 - remaining`, key `monthly` | `normalize_copilot_quota` copilot.py:307-420; `windows["monthly"]` | ✓ |
| §17 fail open on RPC failure/hang/mode unavailable | every path returns None; per-read `wait_for(timeout=5.0)` bounds hangs; `fetch_usage` wraps all in try/except → None | ✓ |
| §17 RPC isolated in probe, scheduler stays provider-agnostic | no orchestrator/usage-manager source changes; only +1 line in tests (parametrize) | ✓ |
| §18 parse `resetDate` → `resets_at`; `next_month_first_day_utc` fallback only when absent/unparseable | `_parse_resets_at` (ISO + epoch s/ms), fallback at copilot.py:400-402 | ✓ |
| §26 test names | all 7 present in `tests/test_copilot_backend.py` (+2 extra standalone-LSP tests) | ✓ |
| §27 test names | all 5 present (including pool-block and running-worker-not-cancelled) | ✓ |
| Global fail-open invariant | `"copilot"` added to `test_usage_probe_failure_never_prevents_dispatch` parametrize in both suite files | ✓ |

## Static hygiene (lint substitute, live ruff denied)

- New imports in `copilot.py` (asyncio, datetime/timezone, json, os, Path, shlex, resolve_bash, terminate_process_tree, get_logger, MAX_LINE_BYTES, UsageWindow) each verified used name-by-name — no F401 candidates.
- New imports in `test_copilot_backend.py` (asyncio, datetime/timezone, json, copilot_module, Issue/Orchestrator/_EligibilityDisposition/RunningEntry, WorkflowState, usage symbols) each verified used — no F401 candidates.
- `test_orchestrator_usage_limits.py` delta is one parametrize element — no import changes.

## Non-blocking observations

- `stderr` of the probe subprocess is never drained; a chatty CLI could block on the pipe — bounded by 5s per-read timeouts and `terminate_process_tree` in `finally`, so fail-open still holds.
- `normalize_copilot_quota` can return an authoritative snapshot with empty `windows` if the premium bucket has no percentage fields; scheduler treats missing windows as no data — no block.
- `_FakeStdin` lacks `is_closing()`; the probe's `finally` guards it with try/except — test-only, harmless.

## Verdict

No CRITICAL/HIGH/MEDIUM findings. Diff is exactly the 4 files in scope; no drive-by edits.
