# TASK-5 AC Scorecard (Verify re-pass 2026-08-15T20:02Z)

One row per ticket acceptance criterion. Live pytest is denied by the
workspace permission policy (`qa/runtime-blocked.md`), so runtime rows are
"pass (indirect)": the last completed pytest session (19:51Z) recorded
`lastfailed = {}` and the 19:54Z collection contains all 23 TASK-5 tests —
see `qa/pytest-cache-evidence.md` for what that proves and does not prove.

| AC | signal | source | result | evidence |
| --- | --- | --- | --- | --- |
| 1 — selection_for_state resolves the 8-tier precedence (dispatch profile > dispatch kind > ticket profile > ticket kind > stage_profiles > stage_kinds > default_profile > agent.kind) | 8 tier tests + frozen-dataclass test collected and in the zero-failure run; static review of `selection_for_state` (config.py:310-399) matches the ticket's precedence list verbatim | pytest cache + diff review | pass (indirect) | `qa/pytest-cache-evidence.md`; card `## Review` |
| 2 — resolve_agent_config overlays non-null profile fields; inherited command intact | overlay/command-override/no-profile/unknown-profile tests in the zero-failure run; static review: `dataclasses.replace(base, **overrides)` copies only allowlisted non-null fields, un-overridden fields (command, stall_timeout_ms) come from the base | pytest cache + diff review | pass (indirect) | `qa/pytest-cache-evidence.md`; card `## Review` |
| 3 — BackendInit carries selection + resolved_backend_config | backend_init tests (explicit + defaulted `__post_init__`) in the zero-failure run; static review: `backends/__init__.py` `__post_init__` defaults both from cfg/selection, 8 backend drivers consume `resolved_backend_config` via isinstance guards | pytest cache + diff review | pass (indirect) | `qa/pytest-cache-evidence.md`; card `## Review` |
| 4 — profile/backend re-resolved on every stage transition | unit stage-transition tests + orchestrator lifecycle test `test_orchestrator_stage_transition_re_resolves_profile` (runs `_run_agent_attempt`, asserts 2 rebuilt backends with different selections) in the zero-failure run; static review: core.py:6989 re-resolves from `base_cfg` per transition | pytest cache + diff review | pass (indirect) | `qa/pytest-cache-evidence.md`; card `## Review` |
| 5 — tickets with both agent_kind and agent_profile rejected as ambiguous | both ambiguity tests (resolver + `_config_for_issue_agent`) in the zero-failure run; static review: guard raises `ConfigValidationError` before any tier lookup | pytest cache + diff review | pass (indirect) | `qa/pytest-cache-evidence.md`; card `## Review` |
| 6 — new tests pass; pre-existing suite green | 23/23 TASK-5 nodeids collected (19:54Z); `lastfailed={}` (19:51Z) = zero failures in the last completed run; 2335 passed + 9 skipped = 2344 matches the collection count exactly; previously-failing symlink-loop chat test (line 479) collected | pytest cache + worker record (`work/details.md`) | pass (indirect; live pytest denied) | `qa/pytest-cache-evidence.md` |
| 7 — backward compatible: only agent.kind + stage_kinds behaves as before | legacy-workflow test in the zero-failure run; static review: for profile-less configs tiers 4/6/8 reproduce the legacy `kind_for_state` (pin > stage_kinds > kind) exactly, and `_config_for_issue_agent` returns the same cfg for the same kind | pytest cache + diff review | pass (indirect) | `qa/pytest-cache-evidence.md`; card `## Review` |

How to re-run (when execution is permitted):
`cd /home/symphony/symphony_workspaces/TASK-5 && .venv/bin/pytest tests/test_workflow_agent_profiles_runtime.py -q && .venv/bin/pytest -q`
