# Static validation review — TASK-13 Verify (2026-08-17)

Per-AC code walk of the committed implementation. All anchors read this pass
via `sed`/`grep` on the worktree files; no runtime execution (see
`qa/runtime-blocked.md`).

## AC1 — usage.py defines ProviderUsageManager, ~60s TTL

- `DEFAULT_CACHE_TTL_S = 60.0` (`src/symphony/orchestrator/usage.py:37`)
- `snapshot()` (`usage.py:58`), `refresh()` async (`usage.py:90`),
  `refresh_if_needed()` (`usage.py:128`), `evaluate()` (`usage.py:150`).
- TTL logic: `expired = last is None or (now_mono - last >= cache_ttl_s)`
  (`usage.py:133`); `force` bypass exists for tests/notifications.

## AC2 — fail open on None / stale / authoritative=False

- `usage.py:154-161`: `snapshot is None` -> READY; `snapshot.stale` -> READY;
  `not snapshot.authoritative` -> READY.
- Reached via `core.py:5528-5560` for any ticket whose pool resolves.

## AC3 — WAIT_PROVIDER_USAGE on hard_limit_reached or window >= cap

- Hard limit: `usage.py:166-178` — WAIT unless every window with a
  `resets_at` has passed it (then not blocking).
- Windows: `usage.py:180-192` — for each configured `pool.caps` entry,
  `actual.used_percent >= cap` -> WAIT; a window whose `resets_at <= now`
  is skipped (fail open).

## AC4 — probe success caches; failure retains last-known + stale; no telemetry fail open

- Success: `usage.py:101-104` stores result in `self.snapshots`.
- `fetch_usage()` returning None: retains last known and sets `stale=True`
  (`usage.py:106-111`).
- Exception: logs, retains last known, sets `stale=True` (`usage.py:112-126`).
- No probe at all (`get_probe` None): returns cached snapshot, no crash
  (`usage.py:95-99`) — evaluate then fails open via AC2.

## AC5 — reset passed AND refresh fails -> fail open

- `refresh_if_needed` detects `resets_at <= now` and forces a refresh
  (`usage.py:134-147`); a failing refresh marks the snapshot stale
  (`usage.py:112-126`); stale evaluate -> READY (AC2).
- Independent belt-and-braces: even WITHOUT a refresh, `evaluate` skips
  blocking on windows whose reset has passed (`usage.py:184-186`), so an old
  blocking snapshot cannot block forever.

## AC6 — core gains _eligibility_usage_decision, inserted ownership -> contract -> usage -> contention

- Definition `core.py:5528-5560`; chain `core.py:5572-5579`:
  ownership (5573) -> contract (5574-5575) -> usage (5576-5577) ->
  contention (5578-5580).
- Profile/pool resolution uses the existing `cfg.selection_for_state` with
  `_requested_agent_profile/_requested_agent_kind` — same selection logic as
  the dispatch path (`core.py:6264-6265`).
- `ConfigValidationError` from selection -> `None` -> no block (fail open).

## AC7 — derived WAIT_NON_SLOT 'waiting_provider_usage', auto-clears

- `core.py:5555-5558`: `_EligibilityDecision(_EligibilityDisposition.WAIT_NON_SLOT,
  "waiting_provider_usage", reason)`.
- No state mutation: nothing writes `_paused_issue_ids` or any persistent
  flag. The decision is recomputed per issue on every dispatch scan
  (`core.py:3934`); when a later tick refreshes the snapshot to under-cap,
  the same code path yields READY. Derived state disappears automatically.

## AC8 — caps never cancel a running worker

- Dispatch scan short-circuits issues in `self._running` BEFORE eligibility:
  `core.py:3893-3903` (`if running is not None: entry.update(status="running",
  ...); continue`).
- Usage evaluation therefore only ever gates NEW dispatches. Nothing in the
  diff touches `cancel_worker` / `_cancel_run` paths.
- The only other `_eligibility_decision` caller is the retry timer
  (`core.py:10171`), which also gates a fresh dispatch, not a live worker.

## AC9 — Stage 6.10/6.11 tests present

- `tests/test_orchestrator_usage_limits.py`: 27 collected ids — same-pool
  block (`test_all_profiles_of_same_pool_are_blocked_by_cap`), other provider
  unaffected, exactly-at-cap blocks / below-cap ready, any-window ×5,
  missing snapshot / probe exception / non-authoritative / no-policy fail
  open, hard limit, stale, reset recovery, failed-refresh-after-reset,
  wrapper shared pool, running worker not cancelled, probe-failure invariant
  ×8 kinds.
- `tests/test_usage_limits.py`: 11 manager unit tests appended
  (`test_provider_usage_manager_*`).
- Runtime proof: `qa/pytest-cache-evidence.md` (indirect),
  `qa/runtime-blocked.md` (fresh run denied).
