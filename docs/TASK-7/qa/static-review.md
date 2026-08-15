# TASK-7 Verify — Static Diff Review & Security Analysis

Method: full read of `git diff main...HEAD` (15 changed paths, +1046/-33) and
targeted greps over the changed files. Runtime execution was gate-refused
(`qa/runtime-blocked.md`).

## Scope map (ticket vs changed paths)

| Plan section | Changed paths | Orphan? |
|---|---|---|
| §12 run records | `orchestrator/migrations.py`, `run_registry.py`, `entries.py`, `core.py`, `diagnostics.py` | no |
| §13 ticket override | `trackers/file.py` | no |
| §14 CLI | `cli/board.py` | no |
| §15 doctor | `cli/doctor.py` | no |
| tests | `test_workflow_agent_profiles_tooling.py` (new, 18 tests), `test_migrations.py`, `test_run_registry.py` (migration-count updates), `test_backend_contract.py` (1-line env fix) | see LOW-4 |
| evidence | `docs/TASK-7/work/*` | no |
| `graphify-out` | symlink (mode 120000) → `/home/symphony/git/oh-my-symphony/graphify-out` | see LOW-1 |

No web UI files touched — changed paths are `.py`/`.md` only.

## Security analysis (per Security Audit row)

- **secrets**: diff adds no credential/token handling; the only grep hits for
  `api_key|password|secret|token` in changed files are pre-existing
  `diagnostics.py:74,89` redaction patterns and token counters. The new
  `_ALLOWED_FIELDS["run_acquired"]` entries (`diagnostics.py:100-107`) expose
  non-secret provenance strings only; existing secret scrubbing still applies.
- **input-validation**: CLI strips profile names and rejects empty
  (`board.py` `cmd_new`), rejects unknown names against
  `WORKFLOW.md` `agent_profiles`, and rejects `--agent-kind` +
  `--agent-profile` together (`cmd_new`/`cmd_update`). Tracker raises
  `SymphonyError("ambiguous agent override…")` for programmatic callers
  (`file.py` `_new_ticket_front`, `update_fields`). Profile config values are
  type/allowlist-validated at parse time (`workflow/builder.py:1040-1105`,
  Phase 3, on main) — unknown backend kind and unsupported fields raise
  `ConfigValidationError`; `symphony doctor` surfaces a load failure as
  `FAIL workflow load failed: …` with exit 2 (`doctor.py:1221-1225`).
  Doctor additionally rejects models containing whitespace and unparseable
  commands (`doctor.py` `check_agent_profiles`).
- **injection**: SQL — all new run-record INSERTs and the widened search
  clause use `?` placeholders with matching param counts
  (`run_registry.py` `acquire_run`, `acquire_continuation_run`,
  `recent_runs`). Command — no `shell=True` anywhere in the changed files
  (grep, 0 hits); `check_agent_profiles` uses `shlex.split` + `shutil.which`
  (no execution); `board.py` new code calls tracker APIs only.
- **xss / csrf**: no HTML, web endpoints, or form handling in the diff.
- **authz**: no authorization checks touched.
- **rate-limit**: no rate-limiting or new network-facing endpoints.

## Review findings (severity)

No CRITICAL/HIGH/MEDIUM findings.

- **LOW-1** — `graphify-out` symlink committed on the branch despite
  `.gitignore:39` (`graphify-out/` only matches dirs). On merge it would land
  an absolute-path symlink on `main`; it is host-local tooling, harmless to
  code/tests. Same class as TASK-6's finding (removed there). Recommend the
  orchestrator exclude it or the next stage drop it; `git rm --cached` was
  gate-refused this pass.
- **LOW-2** — lease-reacquire path (`core.py:2431-2437`) passes only
  `agent_kind`; a reacquired run row loses the profile/model/reasoning_effort
  the `RunningEntry` now carries. Provenance gap in a health-degradation edge
  path; AC1 is still met for the normal acquire/continuation paths.
- **LOW-3** — ticket body `## Self-Critique` and `docs/TASK-7/work/details.md`
  claim doctor validates models against regex `^[a-zA-Z0-9_.:/-]+$`. False:
  no such regex exists anywhere in `src/` (grep, 0 hits). Actual check is
  whitespace-only plus `shlex` parseability; a FAIL-on-bad-model test exists
  and passes. Documentation overclaim, not a behavior defect.
- **LOW-4** — `tests/test_backend_contract.py:504` adds
  `monkeypatch.delenv(GIT_ROOTS_ENV_VAR, raising=False)` — 1-line env-isolation
  hygiene outside ticket scope, in service of "pre-existing suite green".

## Backward-compatibility check

- Migration v9 is additive + idempotent (`_add_column_if_missing`), slices in
  `test_migrations.py` updated for the 9-entry tuple.
- `_record()` reads new columns defensively via `row.keys()`, so pre-v9
  databases read by new code yield `""`.
- `RunRecord`/`RunningEntry`/`acquire_run`/`acquire_continuation_run` gain
  keyword-defaulted fields; all call sites use keyword args (checked), so no
  positional binding breaks.

How to re-run: `git diff main...HEAD` plus the greps listed above
(`grep -n "shell=True" <changed files>`, secrets grep, regex grep) on an
unrestricted checkout.
