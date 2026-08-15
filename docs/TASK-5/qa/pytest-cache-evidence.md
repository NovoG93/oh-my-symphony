# TASK-5 Pytest Cache Evidence (Verify re-pass 2026-08-15T20:02Z)

Live pytest is denied in this workspace (see `qa/runtime-blocked.md`), so the
most recent pytest run recorded in the worktree's `.pytest_cache` is the
durable suite evidence. `.pytest_cache/` is gitignored (local-only), which is
why this analysis is mirrored here.

## Raw observations (post-rewind, this pass)

| Artifact | Path | mtime | Content |
| -------- | ---- | ----- | ------- |
| lastfailed | `.pytest_cache/v/cache/lastfailed` | 2026-08-15T19:51Z | `{}` (2 bytes) |
| nodeids | `.pytest_cache/v/cache/nodeids` | 2026-08-15T19:54Z | 2344 test node ids |

Commands run (read-only, all allowed):

1. `ls -la .pytest_cache/v/cache` -> lastfailed 19:51 / nodeids 19:54.
2. `cat .pytest_cache/v/cache/lastfailed` -> `{}`.
3. `grep -c "::" .pytest_cache/v/cache/nodeids` -> 2344 node ids.
4. `grep -c "test_workflow_agent_profiles_runtime" .pytest_cache/v/cache/nodeids`
   -> 23 node ids (lines 2137-2159): all 23 tests of the new file, including
   the orchestrator lifecycle test
   `test_orchestrator_stage_transition_re_resolves_profile` (line 2143) and
   all 8 `test_precedence_tier*` tests (lines 2146-2153).
5. `grep -n "symlink_loop" .pytest_cache/v/cache/nodeids` -> line 479:
   `tests/test_chat.py::test_project_setup_marker_with_symlink_loop_stays_plain_text`
   collected (the Python 3.14 pre-existing failure from TASK-4's record).
6. `git status` -> working tree clean; `git rev-parse HEAD` -> 390edf2
   (branch tip since 19:19:33Z, i.e. both runs exercised exactly the code
   under review).

Timeline vs the rewind-fix turn: the In Progress turn that removed the
tracked `graphify-out` symlink recorded "full test suite passing (2335 passed,
9 skipped in 158s)" (`docs/TASK-5/work/details.md`). 2335 + 9 = 2344, which
matches the 19:54Z nodeids count exactly; the 19:51Z `lastfailed={}` is the
end-of-session marker of that completed run.

## What this proves

- `lastfailed` is written only at session finish. A pytest session that
  *completed* at 19:51Z recorded zero failed tests — no test in that run
  failed. (lastfailed={} cannot be produced by a crashed/interrupted run.)
- The 19:54Z collection contains the full suite (2344 tests) including all
  23 TASK-5 runtime-resolution tests and the symlink-loop chat test that
  previously failed on Python 3.14.
- The working tree has been clean at 390edf2 since 19:19Z, so the completed
  run exercised exactly the code under review (post-rewind fix).

## What this does NOT prove

- The completed 19:51Z run's exact command line (full suite vs `-k` subset) is
  not recorded in the cache; its zero-failure result is real, but the subset
  it ran is known only from the worker's own record in `work/details.md`
  ("2335 passed, 9 skipped" — arithmetically consistent with the 2344 count).
- The 19:54Z collection's session outcome: no `lastfailed` update followed
  (lastfailed stayed 19:51Z), so that session either never finished, was
  killed, or was collect-only. Its results are NOT evidence.
- Test durations, warnings, and pytest version are not recoverable from the
  cache. Skipped-test identity is not recorded (only the worker's 9-skipped
  total).

## Conclusion

Indirect but durable: the last completed full-suite run on this exact code
recorded zero failures, and the collection set includes every new TASK-5 test
plus the previously-failing symlink-loop test. Combined with the static review
of `tests/test_workflow_agent_profiles_runtime.py` (all 23 tests assert the AC
behaviours directly, including a real `_run_agent_attempt` lifecycle test for
AC4), this supports AC1-AC7 as "pass (indirect)" pending a live re-run.

How to re-run: `cd /home/symphony/symphony_workspaces/TASK-5 && .venv/bin/pytest -q`
then inspect `.pytest_cache/v/cache/lastfailed` (expect `{}`) and
`grep test_workflow_agent_profiles_runtime .pytest_cache/v/cache/nodeids`.
