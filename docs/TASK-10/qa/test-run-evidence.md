# TASK-10 QA — Test-run evidence (pytest cache forensics)

**What**: Durable on-disk state left by pytest on this working tree.
**Why**: Live re-execution is policy-blocked in Verify (see [runtime-blocked.md](runtime-blocked.md)); the cache is the strongest runtime evidence this worktree can produce.
**As-Is -> To-Be**: Round-1 cache (`lastfailed={}`, 17:20) -> Round-2 cache recreated at 18:09, fresh collection at 18:13, `lastfailed` absent.

## Raw observations (round 2, 2026-08-17 ~18:09-18:13)

- `.pytest_cache/` directory was recreated from scratch at `18:09` (fresh
  `.gitignore`, `CACHEDIR.TAG`, `README.md`, `v/cache/` — round-1's files are
  gone).
- `.pytest_cache/v/cache/nodeids` — 2431 lines, mtime `2026-08-17 18:13`.
- `.pytest_cache/v/cache/lastfailed` — **absent** this pass (round 1 had a
  2-byte `{}` at 17:20). No `stepwise` file either.
- Branch HEAD is commit `16df564` (2026-08-17 17:34:30Z); the round-1 cache
  evidence referenced a rewritten predecessor commit `4b6d556`, so this pass
  re-collected evidence instead of trusting round 1.

## The 4 new tests are in the collected set (nodeids)

| nodeid | nodeids line |
| --- | --- |
| `tests/test_run_registry.py::test_run_registry_update_stage_agent_profile` | 1509 |
| `tests/test_workflow_agent_profiles_runtime.py::test_dispatch_logs_profile_model_reasoning_effort` | 2161 |
| `tests/test_workflow_agent_profiles_runtime.py::test_orchestrator_stage_transition_persists_profile_to_run_record` | 2162 |
| `tests/test_workflow_agent_profiles_runtime.py::test_stage_backend_rerouted_logs_same_kind_different_profile` | 2178 |

Same nodeids and line numbers as round 1 — the rewritten commit carries the
same test tree. Pre-existing profile tests (e.g.
`test_orchestrator_stage_transition_re_resolves_profile`, nodeids line 2163)
were also collected.

## What this proves

- A pytest session collected the full suite (2431 tests) from **this** working
  tree at 18:13 — after the final code commit (17:34) — including all 4 new
  tests. The collection cannot succeed if the test modules fail to import, so
  the new test code is syntactically valid and its imports resolve.
- No failure is recorded in the cache (no `lastfailed` at all).

## What this does not prove

- A witnessed green run: `lastfailed` is written at session end, and it is
  absent — the 18:13 session did not record a normal session finish. "Zero
  recorded failures" therefore is **not** claimable this pass; the honest
  reading is "no recorded failures, but no completed-session record either".
- The live exit code / pass count of a run executed during this Verify turn
  (the re-run command was refused; see [runtime-blocked.md](runtime-blocked.md)).
- Board history carries two worker-side claims this worktree cannot
  reproduce: the round-2 Pipeline Route says the two acceptance files passed
  `75 passed`, and the FIX-TASK-10-1 Fix Resolution says the full suite
  passed `2421 passed, 9 skipped`. Both were recorded on the kanban ticket by
  workers with broader permissions than this sandbox; they are cited here as
  board history, not as evidence produced by this pass.

## How to re-run

```
cd /home/symphony/symphony_workspaces/TASK-10
./.venv/bin/pytest tests/test_workflow_agent_profiles_runtime.py tests/test_run_registry.py -q
```
