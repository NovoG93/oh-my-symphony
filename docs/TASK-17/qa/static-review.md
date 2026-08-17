# Static Review: full diff read (Verify, 2026-08-17)

**What**: Line-by-line review of the TASK-17 delta against the ticket, its Plan, Acceptance Tests, and Done Signals.
**Why**: Compensate for the denied live re-run with a complete static read of every changed line.
**As-Is -> To-Be**: Unreviewed delta -> Every changed line classified, no orphan scope.

## Delta (`git diff --name-only develop..HEAD`, 115223c..f44482b)

7 paths, all ticket scope:

| Path | +/- | Classification |
|---|---|---|
| `tests/test_usage_limits.py` | +105 | Stage 6.2 generic pool tests (5 functions, 1 parametrized x4 = 8 nodeids) |
| `README.md` | +17/-2 | `usage_pools` section: HOW/WHETHER boundary, shared quota, fail-open, worker non-interruption, exhaustion, UI card |
| `WORKFLOW.example.md` | +27/-1 | commented `usage_pools:` block + `usage_pool:` examples + profile field lists |
| `WORKFLOW.file.example.md` | +27/-1 | same as above (file-WORKFLOW variant) |
| `docs/features/agent-profiles.md` | +7 | Stage 6 test-suite + global fail-open invariant paragraph |
| `docs/TASK-17/work/details.md` | +74 | ticket evidence (new file) |
| `docs/TASK-17/work/plan.md` | +36 | ticket evidence (new file) |

No `src/` changes, no unrelated test edits, no drive-by refactors. Orphan-scope check: none.

## Doc claims cross-checked against code

- README:233-234 "profiles define HOW / pools define WHETHER" — matches pool resolution `core.py:5635-5637` (pool carries caps; profile only references it).
- README "Dedicated backends default their pool to backend kind" — `(profile_cfg.usage_pool ...) or selection.kind`; omission on multiplexing kinds (`pi`, `prime-agent`) stays None and fails open (pool lookup returns None -> eligible). Matches `test_prime_claude_does_not_implicitly_use_claude_code_pool`.
- README "caps never cancel a running worker" — scheduler-only enforcement (`_eligibility_usage_decision`); covered by `test_configured_cap_does_not_cancel_running_worker`.
- README `EVENT_PROVIDER_USAGE_EXHAUSTED` — real constant (`src/symphony/backends/__init__.py:50`), emitted in `per_turn.py:307`, `pi.py:330`.
- WORKFLOW examples: `usage_pools:`/`usage_pool:` keys match the config schema names used by `test_usage_limits.py` fixtures (`UsagePoolConfig`, `caps:` windows `five_hour`/`weekly`).

## Test-behavior cross-check

- New tests assert `ProviderUsageManager.evaluate` semantics (`src/symphony/orchestrator/usage.py:150-190`): snapshot missing -> READY (line 158-159); stale -> READY; non-authoritative -> READY; `used_percent >= cap` -> WAIT_PROVIDER_USAGE; reset passed -> not blocking. All four new assertions match these branches.
- Fail-open invariant test (`test_backend_usage_probes.py:538-576`): installs a raising probe + pops the snapshot, asserts `_eligibility_usage_decision(...) is None` (None = not usage-blocked -> dispatch proceeds). Structure: probe exceptions can never fabricate a snapshot; every snapshot-less/stale/non-authoritative path returns READY. Invariant holds structurally for all 8 parametrized kinds (16 nodeids across 2 modules, green in `qa/test-cache-evidence.md`).

## Findings

None. Claims in `docs/TASK-17/work/details.md` are consistent with the tree except one timing note: it lists `docs/llm-wiki/usage-aware-agent-profiles.md` as "updated with Stage 6 summary" — that wiki write-back is the **Document** stage's duty (wiki files are written inside the worktree and delivered by the Done merge); the file currently reflects TASK-16. Hand-off note left for Document.
