# TASK-10 QA — Test-run evidence (pytest cache forensics)

**What**: Durable on-disk state left by the pytest runs executed during the In Progress turn on this working tree.
**Why**: Live re-execution is policy-blocked in Verify (see [runtime-blocked.md](runtime-blocked.md)); this is the strongest runtime evidence that the suite ran on the final code.

## Raw observations

- `.pytest_cache/v/cache/lastfailed` — 2 bytes, content `{}`, mtime `2026-08-17 17:20`.
- `.pytest_cache/v/cache/nodeids` — 2431 lines, mtime `2026-08-17 17:21`.
- The branch's single In Progress commit is `4b6d556` at `2026-08-17 17:21:15Z` —
  the cache files were written in the same minute, i.e. during/after the final code state.

## The 4 new tests are in the collected set (nodeids)

| nodeid | nodeids line |
| --- | --- |
| `tests/test_run_registry.py::test_run_registry_update_stage_agent_profile` | 1509 |
| `tests/test_workflow_agent_profiles_runtime.py::test_dispatch_logs_profile_model_reasoning_effort` | 2161 |
| `tests/test_workflow_agent_profiles_runtime.py::test_orchestrator_stage_transition_persists_profile_to_run_record` | 2162 |
| `tests/test_workflow_agent_profiles_runtime.py::test_stage_backend_rerouted_logs_same_kind_different_profile` | 2178 |

Pre-existing profile tests (e.g. `test_orchestrator_stage_transition_re_resolves_profile`,
nodeids line 2163) were also collected.

## What this proves

- A pytest session collected the full suite (2431 tests) from **this** working
  tree after the implementation was final, including all 4 new tests.
- The last recorded failure state is the empty set (`lastfailed = {}`) — no
  test failure was recorded for this tree.

## What this does not prove

- The live exit code / pass count of a run executed during this Verify turn
  (the re-run command was refused; see [runtime-blocked.md](runtime-blocked.md)).
- That the 17:21 collection run completed end-to-end: `nodeids` is written at
  collection, `lastfailed` at session end. The 17:20 `lastfailed={}` could
  belong to an earlier run, and the 17:21 run could in principle have been
  interrupted after collection. "Zero recorded failures" is therefore
  indirect, not a witnessed green run.

## How to re-run

```
cd /home/symphony/symphony_workspaces/TASK-10
./.venv/bin/pytest tests/test_workflow_agent_profiles_runtime.py tests/test_run_registry.py -q
```
