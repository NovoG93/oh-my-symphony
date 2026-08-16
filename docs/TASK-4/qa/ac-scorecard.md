# TASK-4 Verify — Acceptance Criteria Scorecard (2026-08-15T18:50Z)

One row per acceptance criterion from the ticket description. Source anchors
and prose live in the cited artifacts; this file only scores them.

| AC | Signal | Source | Result | Evidence |
|---|---|---|---|---|
| AC-1 dataclass with 9 fields, all-but-name/kind Optional, frozen | `@dataclass(frozen=True)` at config.py:103-121; optional fields default None | `git diff 4c9e7b1 HEAD -- src/symphony/workflow/config.py` | pass | `qa/review-notes.md` (diff reviewed in full); `work/model_design.md` (field table) |
| AC-2 `agent_profiles:` + `agent.stage_profiles:` + `agent.default_profile:` parse | test_parse_valid_agent_profiles_and_agent_routing asserts all three populated | executed in the 18:42Z run (nodeids entry, not in lastfailed) | pass (runtime) | `qa/qa-evidence.md` row 7; `tests/test_workflow_agent_profiles.py:63` |
| AC-3 validation rejections (empty/dup names, unknown kind, missing refs, non-string model, non-positive timeouts, per-kind allowlist) | one test per rejection class; duplicate guard at builder.py:1011 | static diff review + 15/16 tests executed with zero profile failures | pass; duplicate-name test = static-only, Not proven by runtime | `qa/review-notes.md` (guard + test cited); `qa/qa-evidence.md` row 7 |
| AC-4 malformed profile config fails at config/validation time | helpers raise `ConfigValidationError` inside `build_service_config` (builder.py:300, 434-442) | diff review; error-path tests (missing kind, unknown profile) executed and passing | pass | `qa/review-notes.md`; `qa/qa-evidence.md` row 7 |
| AC-5 new tests pass + pre-existing suite green | 15/16 new tests executed, zero profile failures; full suite had 1 branch-unrelated pre-existing failure | `.pytest_cache` lastfailed/nodeids | pass with caveat (16th test unexecuted; live pytest denied) | `qa/qa-evidence.md` rows 7-8 + failure triage |
| AC-6 no runtime dispatch/backend change | grep: new keys read only by config/builder/constants/__init__ | `qa/review-notes.md` (Scope check) | pass | `qa/review-notes.md`; `qa/security-audit.md` (injection row) |

Notes:
- AC-3's duplicate-name rejection rests on static review only — the test
  was added in the last fix commit after the final recorded run
  (`.pytest_cache` contains 15, not 16, profile test IDs). Re-run command:
  `pytest tests/test_workflow_agent_profiles.py -q`.
- AC-5's "suite green" is Not proven by live run (pytest denied twice, both
  forms, two passes — `qa/runtime-blocked.md`).
