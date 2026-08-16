# TASK-8 Verify — Recorded Pytest Session Evidence

Live pytest re-runs are refused by the workspace permission gate
(`qa/runtime-blocked.md`). This file reconstructs the last recorded test
session from `.pytest_cache/v/cache/` plus file mtimes.

## Timestamped facts (2026-08-16, UTC, `ls --time-style=full-iso`)

| Event | mtime | Meaning |
|---|---|---|
| `tests/test_workflow_agent_profiles_e2e.py` | 05:07:04.725 | last edit of the new test file |
| `tests/__pycache__/test_workflow_agent_profiles_e2e.cpython-314-pytest-9.1.1.pyc` | 05:07:07.134 | pytest imported/compiled the **final** file content (pyc newer than source) |
| `.pytest_cache/v/cache/lastfailed` | 05:07:07.699 | content `{}` (2 bytes) — zero recorded failures |
| `.pytest_cache/v/cache/nodeids` | 05:07:11.067 | 2,380 collected node ids |
| ticket `kanban/TASK-8.md` `updated_at` | 05:07:30 | handoff to Verify right after the session finished |

## What the cache files mean (from this venv's pytest source)

`.venv/lib/python3.14/site-packages/_pytest/cacheprovider.py`:
- lines 415-423: `cache/lastfailed` is written at session finish **only when
  it differs** from the saved value. `{}` written at 05:07:07.699 means the
  finishing session ended with an empty failure set.
- lines 460-469: `cache/nodeids` is rewritten at **every** session finish
  (except `--collect-only`), as the sorted union of collected ids across
  sessions. 2,380 ids at 05:07:11.067 = the accumulated collection set.

The `.pyc` (05:07:07.134) is newer than the `.py` (05:07:04.725): Python
compiles at import when the source is newer, so the finishing session imported
the exact final committed content of the new test file.

All 8 tests of the new file are in the collected set (unique ids):
`test_section_20_acceptance_config_parsing_and_resolution`,
`test_backward_compatibility_legacy_workflow_with_stage_kinds`,
`test_multi_model_same_backend_profiles`,
`test_migration_stage_profiles_precedence_over_stage_kinds`,
`test_doctor_passes_section_20_acceptance_config`,
`test_pure_legacy_workflow_with_no_stage_routing`,
`test_full_8_tier_precedence_hierarchy_e2e`,
`test_ticket_ambiguity_override_rejected_e2e`.

## Coverage of the final tree

`git status` reports the working tree clean, so the executed code+test tree is
byte-identical to branch tip `95f3aec`. The only files edited after the
recorded session are documentation (`README.md`, `README.ko.md`,
`docs/features/agent-profiles.md`, `docs/TASK-8/work/details.md` — the two LOW
fixes in `qa/static-review.md`); no source or test file changed, so the
recorded green result still covers the tree that will merge.

## Proves / does not prove

- Proves: a pytest session on the final test file finished at 05:07:07.699Z
  with an empty failure set, after importing the final file content
  (pyc mtime chain), and the collected set contains all 8 new tests with the
  exact names of the committed file.
- Proves (indirect): "the 8 new E2E tests pass" — collected ids + empty
  `lastfailed`.
- Does not prove: a live re-run by this Verify pass (gate refused); per-test
  outcomes of all 2,380 collected ids in one single green run; exact
  pass/skip counts and exit code. The ticket's "2367 passed, 9 skipped" claim
  (2367+9=2376) is 4 short of the 2,380 collected ids — plausibly
  deselects/skips in earlier sessions; not a failure signal
  (same class as TASK-7's count discrepancy).

How to re-run: `.venv/bin/pytest tests/test_workflow_agent_profiles_e2e.py -v`
(ticket tests) or `.venv/bin/pytest -q` (full suite) on an unrestricted
checkout.
