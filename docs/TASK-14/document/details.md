# TASK-14 Document — Details (brief vs reality comparison)

**What**: The Document-stage comparison of the ticket's claims against the committed code, and the doc/wiki updates made.
**Why**: Document must verify the brief matches reality before closing; overflow lives here because ticket sections are capped.
**As-Is -> To-Be**: Ticket claims unverified by Document -> Each claim anchored to the commit, with nuances on record.

## What was verified (all claims hold)

- **Diff identity**: `git log 8353534..HEAD` = one commit `841d818` ("wip: turn 2026-08-17T20:37:33Z", parent = develop tip `8353534`). Its tree is byte-identical to the implementation commit `b849565` referenced by the QA evidence for the 8 implementation files; the tip additionally carries the 5 Verify doc files (13 files total, all TASK-14 scope).
- **AC1 probe**: `CodexUsageProbe.fetch_usage` (`codex.py:449`) calls `account/rateLimits/read` via client, backend, or standalone subprocess; returns `ProviderUsageSnapshot`; fails open on error. Test: `test_codex_usage_probe_calls_rate_limits_read`.
- **AC2 duration normalization**: `normalize_codex_rate_limits` (`codex.py:278`) keys windows by `windowDurationMins` (300 -> `five_hour`, 10080 -> `weekly`, other -> `<N>_minutes`); position ignored. Tests: `test_codex_detects_windows_by_duration_not_position`, `test_codex_normalizes_five_hour_window`, `test_codex_unknown_window_is_preserved_or_ignored_safely`.
- **AC3 immediate cache update**: `account/rateLimits/updated` -> `_handle_notification` (`codex.py:1113`) -> `usage_manager.set_snapshot` (`codex.py:1157`); orchestrator `_on_codex_event` also normalizes incoming `rate_limits` payloads. Test: `test_codex_updated_notification_updates_shared_pool`.
- **AC4 apiKey cap exemption**: apiKey/accountType auth -> `authoritative=False` in `normalize_codex_rate_limits`. Test: `test_codex_api_key_auth_does_not_apply_chatgpt_cap`.
- **AC5 event + error type**: `EVENT_PROVIDER_USAGE_EXHAUSTED = "provider_usage_exhausted"` (`backends/__init__.py:50`); `ProviderCapacityError(pool_id, resets_at, message)` dataclass (`backends/__init__.py:58`). Test: `test_provider_capacity_error_dataclass_and_event_constant`.
- **AC6 genuine-only**: `_is_genuine_provider_exhaustion` (`codex.py:398`) returns False for RPM/TPM text; exhaustion keywords -> True; emission in `_raise_for_terminal_status` (`codex.py:927`) before generic `EVENT_TURN_FAILED`/`TurnFailed`. Tests: `test_genuine_provider_exhaustion_detection`, `test_generic_429_rpm_treated_as_normal_retry_not_provider_exhaustion`.
- **AC7 retry-budget bypass**: `_on_codex_event` exhaustion branch (`core.py:8961`) writes hard-limit snapshot + entry flags + cancels worker; `_run_agent_attempt` catches `ProviderCapacityError` (`core.py:7392`) and returns `provider_usage_exhausted`; `_on_worker_exit_impl` (`core.py:9874`) pops retry trackers and the claim without consuming attempt counts or setting pause flags -> next tick derives `waiting_provider_usage`. Test: `test_provider_exhaustion_does_not_consume_retry_budget`.
- **AC8 test evidence**: 15 tests present in `tests/test_codex_usage.py` with the committed names; pytest cache (20:37 UTC) shows 2514 collected, `lastfailed = {}`. Fresh re-run not proven (execution denied — `qa/runtime-blocked.md`).
- **Registry**: `USAGE_PROBES["codex"] = CodexUsageProbe` at codex.py import AND lazy import in `get_usage_probe` (`usage.py`) — idempotent dual registration; `USAGE_PROBES` no longer empty.

## Nuances found (not defects)

- The ticket's `## Merge Status` "8 files / one commit b849565" describes the implementation commit; the mergeable branch tip `841d818` carries 13 files (implementation + Verify docs). Topology claim unchanged: develop is a direct ancestor, linear single-commit branch, no conflict possible.
- Notification-path snapshots default `authoritative=True` (payload has no authMode); the apiKey guarantee is enforced on the probe path. Already documented as LOW in `verify/details.md`.
- Cosmetic double blank lines in `entries.py`, `codex.py`, `core.py` — formatting only, already noted as LOW.
- Done-Signal figure "2504 tests" vs 2514 collected nodeids: cache is authoritative for the committed code.

## Docs and wiki updates made

- `docs/features/agent-profiles.md` §Usage Pools: replaced the stale "Provider quota probes ... land in later stages" sentence with Stage 2.1 (probe, duration normalization, apiKey non-authoritative) + Stage 4 (capacity wait, retry-budget bypass) reality.
- `docs/llm-wiki/usage-aware-agent-profiles.md`: new "Stage 2.1 + Stage 4" section; Stage-3 boundary marked superseded; decision-log row for TASK-14; "Last updated" bumped.
- `docs/llm-wiki/INDEX.md`: refreshed the `usage-aware-agent-profiles` row summary + last-touched.
- README.md / README.ko.md / CHANGELOG.md: no change — the pool-caps paragraph remains accurate and changelog entries are cut at release commits (per `release-version-bump` wiki), not per ticket.

## How to re-run

```
git log 8353534..HEAD                                   # expect 1 commit, parent = develop tip
git diff 8353534..HEAD --stat                           # 13 files, all TASK-14 scope
grep -n "^def test_\|^async def test_" tests/test_codex_usage.py   # 15 tests
cat .pytest_cache/v/cache/lastfailed                    # expect {}
grep -c . .pytest_cache/v/cache/nodeids                 # 2514 collected
.venv/bin/pytest -q                                     # fresh run (denied this session)
```
