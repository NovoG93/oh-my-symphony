# TASK-6 Verify — Test-Run Evidence (re-verify pass)

## Goal

Record the durable proof that the 9 new backend tests pass and the
pre-existing suite is green on the final TASK-6 tree, together with exactly
what that proof does and does not establish. Live pytest execution is denied
in this workspace (see `qa/runtime-blocked.md`), so this pass relies on the
suite session recorded in `.pytest_cache` and its correlation with the host
log and file mtimes. Written 2026-08-15.

## The evidence chain

1. `.pytest_cache/v/cache/nodeids` — 2354 node ids, including all **9**
   TASK-6 tests (`test_codex_profile_model_and_reasoning_effort_in_turn_params`,
   `test_codex_inherited_command_and_profile_command_override`,
   `test_claude_inject_model_helper_cases`,
   `test_claude_profile_model_injected_in_run_turn`,
   `test_claude_inherited_command_and_profile_override`,
   `test_claude_resume_and_timeout_inheritance`,
   `test_session_scoping_different_profiles_same_backend`,
   `test_session_scoping_claude_models_distinct_sessions`,
   `test_dispatch_refuses_ambiguous_and_unknown_agent_profile_without_raising`).
   mtime **2026-08-15 20:40:50Z**.
2. `.pytest_cache/v/cache/lastfailed` — **empty** (0 lines), mtime
   2026-08-15 20:37:29Z.
3. pytest cache semantics (read from the installed source,
   `.venv/lib/python3.14/site-packages/_pytest/cacheprovider.py`):
   `NFPlugin.pytest_sessionfinish` writes `nodeids` at **session finish**
   (only skipped for `--collect-only`, lines ~455-469); `LFPlugin.pytest_sessionfinish`
   rewrites `lastfailed` **only when it changed** (lines ~418-423).
4. Host log `/home/symphony/git/oh-my-symphony/log/symphony.log`: TASK-6
   turns 2 and 4 end with the agent reporting it launched the full test
   suite in the background ("I have launched the full test suite in the
   background…", ts 20:23:13; turn 4 completed ts 20:40:54).
5. Final file mtimes in the worktree: `core.py` 20:36:37, the test file
   20:38:42, `claude_code.py` 20:19:17 — **all before 20:40:50**, and
   `git status` is clean, so the tree at 20:40:50 is byte-identical to
   commit `59e34e8` (the current HEAD).

## What this proves

- A full-suite pytest session **completed normally** (it reached
  `pytest_sessionfinish`) on the final tree at 20:40:50 — the nodeids write
  is the session-finish artifact, and the collection included the regression
  test added at 20:38:42, so the session started after every final edit.
- That session recorded **zero failures**: had any test failed,
  `lastfailed` would have changed from `{}` and been rewritten at
  ~20:40:50 (cacheprovider writes on change only); its mtime stayed
  20:37:29 and its content is empty.
- All 9 new TASK-6 tests were collected and passed in that session (present
  in nodeids, absent from lastfailed). Pre-existing suite: 2345 other tests
  likewise passed (nothing recorded as failed).

## What this does not prove

- Exact pass counts (e.g. the "2344 passed" figure in the ticket's
  `## Acceptance Tests`) — the cache stores node ids and failures, not
  per-test pass records; 2354 collected vs 2344 passed is consistent with
  10 skipped/deselected but is not independently verifiable here.
- The suite process exit code — it is not logged anywhere found; one raw
  log line ("child process pid 403847 … returncode 255", ts 20:40:54)
  coincides with the turn-4 agent CLI child exiting and is followed by a
  normal `agent_turn_completed`; it cannot be attributed to pytest, and
  pytest itself records zero failures for that window.
- A live re-run by this Verify pass — pytest was denied (see
  `qa/runtime-blocked.md`).

## How to re-run

```
env -u SYMPHONY_GIT_WRITABLE_ROOTS .venv/bin/pytest tests/test_workflow_agent_profiles_backend.py -v
env -u SYMPHONY_GIT_WRITABLE_ROOTS .venv/bin/pytest -q
```
