# Test-Cache Evidence: pytest run record (Verify, 2026-08-17)

**What**: Analysis of the worktree's `.pytest_cache` from the implementation turn.
**Why**: Live pytest is denied by the permission policy (see `qa/runtime-blocked.md`); the cache is the durable record of the most recent suite run.
**As-Is -> To-Be**: No run evidence -> Counts + failure-state analysis with explicit caveats.

## Raw data (mirrored from `.pytest_cache/v/cache/`; the cache dir is git-ignored)

- `nodeids` — mtime **2026-08-17 21:54:37**, contains **2564** collected test ids (counted with `grep -c '"tests/'` — the file is pretty-printed JSON; `wc -l` would count the `[`/`]` brackets too).
- `lastfailed` — **absent** (no file). The venv's pytest (`_pytest/cacheprovider.py:421-423`) writes `lastfailed` only when the recorded failure set *changes* during a run; the cache dir was created 2026-08-17 21:51:13 and the nodeids write at 21:54:37 came from a *completed* run (`--collect-only` sessions skip the nodeids write, line 465-466). A run with any failure would have written a non-empty `lastfailed`. Absence => the last completed run ended with **zero failures**.
- Working tree committed by the harness at **2026-08-17 21:54:58** (`f44482b wip`), 21 s after the run finished; the working tree is clean, so the committed code equals the code that run executed.

## Counts relevant to the Done Signals / ACs

- Usage-suite selection (the 8 files matching AC1 + the two backend probe suites): `test_usage_limits.py` 48 + `test_backend_usage_probes.py` 36 + `test_codex_usage.py` 15 + `test_orchestrator_usage_limits.py` 27 + `test_workflow_agent_profiles.py` 16 + `test_webapi.py` 97 + `test_web_static_contract.py` 24 + `test_i18n.py` 23 = **286** nodeids — matches the ticket's "286 passed" figure exactly; none in `lastfailed`.
- All 8 new Stage 6.2 ids present in `test_usage_limits.py`: `test_profiles_with_same_usage_pool_share_limit`, `test_pi_copilot_is_not_blocked_by_codex_limit`, `test_any_configured_window_can_block[daily|five_hour|monthly|weekly]` (x4), `test_estimated_usage_never_blocks_scheduler`.
- Fail-open invariant parametrizations: `test_usage_probe_failure_never_prevents_dispatch[...]` appears with **all 8 kinds** (`codex`, `claude`, `agy`, `gemini`, `kiro`, `opencode`, `pi`, `prime-agent`) in **both** `test_backend_usage_probes.py` and `test_orchestrator_usage_limits.py` (16 nodeids total).
- Full suite: 2564 collected. The ticket's "2548 passed, 9 skipped" figures describe the implementation turn's earlier run before the 8 Stage 6.2 tests existed (2557 collected then); the final recorded run collected 2564 with zero failures.

## What this proves / does not prove

- **Proves**: a completed full-suite run at 21:54:37 collected the exact committed tree (clean working tree, commit 21:54:58), including the 8 new Stage 6.2 tests and all 16 fail-open parametrizations, and ended with zero recorded failures. The 286-test usage selection is fully present and green in that record.
- **Does not prove**: a *fresh* re-run on the exact committed SHA from this Verify pass (denied — `qa/runtime-blocked.md`); pass counts per test; that no test was skipped in the final run (skip status is not recorded in the cache).

**How to re-run**: in a checkout of `symphony/TASK-17` outside the worktree sandbox: `.venv/bin/pytest tests/test_usage_limits.py tests/test_workflow_agent_profiles.py tests/test_orchestrator_usage_limits.py tests/test_webapi.py tests/test_web_static_contract.py tests/test_i18n.py tests/test_backend_usage_probes.py tests/test_codex_usage.py -q` (expect `286 passed`), then `.venv/bin/pytest -q` (expect `2564` collected, 0 failures).
