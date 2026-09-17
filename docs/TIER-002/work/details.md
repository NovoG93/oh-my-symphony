# TIER-002 Work Details: Fix invalid deep-planner multi-blocker syntax

## Overview
- **Ticket**: TIER-002
- **Title**: Fix invalid deep-planner multi-blocker syntax
- **Branch**: `symphony/TIER-002`
- **Scope**: Deep planner prompt (`docs/symphony-prompts/file/deep/plan.md`) and preset test assertions (`tests/test_workflow_presets.py`).

## Plan
### User Goal
Ensure that the deep planner prompt example for creating multi-parent tickets uses valid Symphony CLI syntax (repeated `--blocked-by` options) rather than comma-separated arguments, which the CLI rejects as malformed identifiers.

### As-Is State
- In `docs/symphony-prompts/file/deep/plan.md:19`, the example command specifies `--blocked-by BUILD-1,BUILD-2`.
- `symphony board new` parses `--blocked-by` using `action="append"` and validates each identifier against `^[A-Za-z][A-Za-z0-9_-]{0,63}$`. Passing `BUILD-1,BUILD-2` fails validation with `error: blocked_by target must match ^[A-Za-z][A-Za-z0-9_-]{0,63}$ (identifier='BUILD-1,BUILD-2')`.
- `tests/test_workflow_presets.py` only asserts generic `--blocked-by` presence, without asserting repeated flag syntax or forbidding comma-separated syntax.

### To-Be Target State
- `docs/symphony-prompts/file/deep/plan.md:19` uses `--blocked-by BUILD-1 --blocked-by BUILD-2`.
- `tests/test_workflow_presets.py` contains regression tests requiring `--blocked-by BUILD-1 --blocked-by BUILD-2` and asserting absence of `--blocked-by BUILD-1,BUILD-2`.
- `deep/plan.md` remains succinct (<= 45 lines; line count = 27).
- No production files outside `docs/symphony-prompts/file/deep/plan.md` and no test files outside `tests/test_workflow_presets.py` are modified.

## Acceptance Tests
1. `tests/test_workflow_presets.py`:
   - Extend `test_deep_prompts_are_succinct_and_carry_the_gates` to assert `--blocked-by BUILD-1 --blocked-by BUILD-2` in `plan` and `--blocked-by BUILD-1,BUILD-2` not in `plan`.
   - Add `test_deep_plan_prompt_multi_blocker_syntax` to independently verify the syntax requirements.
2. Verification commands:
   - `.venv/bin/python -m pytest -v tests/test_workflow_presets.py`
   - `.venv/bin/ruff check src tests`
   - `.venv/bin/symphony-pyright`
   - `env -u SYMPHONY_API_AUTH_MODE -u SYMPHONY_API_TOKEN_FILE -u SYMPHONY_API_TOKEN -u SYMPHONY_TRUSTED_ORIGINS -u SYMPHONY_REMOTE_OPERATOR_CAPABILITIES .venv/bin/python -m pytest -q`
   - `git diff --check`

## Done Signals
- Focused test fails RED before the prompt update: 2 failed.
- Focused test passes GREEN after the one-line prompt update: 8 passed.
- `ruff check src tests`: All checks passed!
- `symphony-pyright`: 0 errors, 0 warnings, 0 informations.
- Full pytest test suite passes in sanitized environment: 2927 passed, 14 skipped.
- Prompt line count remains within budget: 27 lines (budget <= 45).
- `Not proven`: Live ticket creation on external trackers (Jira/Linear) as this ticket addresses prompt documentation and CLI compatibility.

## TDD Execution Evidence

### 1. Focused Tests (RED Phase)
Command:
```bash
.venv/bin/python -m pytest -v tests/test_workflow_presets.py -k "multi_blocker or test_deep_prompts_are_succinct"
```
Output:
```text
============================= test session starts ==============================
collected 8 items / 6 deselected / 2 selected

tests/test_workflow_presets.py::test_deep_prompts_are_succinct_and_carry_the_gates FAILED [ 50%]
tests/test_workflow_presets.py::test_deep_plan_prompt_multi_blocker_syntax FAILED [100%]

=================================== FAILURES ===================================
______________ test_deep_prompts_are_succinct_and_carry_the_gates ______________
    ...
>       assert "--blocked-by BUILD-1 --blocked-by BUILD-2" in plan
E       assert '--blocked-by BUILD-1 --blocked-by BUILD-2' in '...'
tests/test_workflow_presets.py:97: AssertionError
__________________ test_deep_plan_prompt_multi_blocker_syntax __________________
    ...
>       assert "--blocked-by BUILD-1 --blocked-by BUILD-2" in plan
E       assert '--blocked-by BUILD-1 --blocked-by BUILD-2' in '...'
tests/test_workflow_presets.py:147: AssertionError
======================= 2 failed, 6 deselected in 0.25s ========================
```

### 2. Prompt Correction (GREEN Phase)
Updated `docs/symphony-prompts/file/deep/plan.md:19` from `--blocked-by BUILD-1,BUILD-2` to `--blocked-by BUILD-1 --blocked-by BUILD-2`.

Command:
```bash
.venv/bin/python -m pytest -v tests/test_workflow_presets.py
```
Output:
```text
============================= test session starts ==============================
collected 8 items

tests/test_workflow_presets.py::test_two_presets_ship PASSED             [ 12%]
tests/test_workflow_presets.py::test_default_preset_matches_shipped_four_lane_board PASSED [ 25%]
tests/test_workflow_presets.py::test_deep_preset_declares_eight_lane_pipeline PASSED [ 37%]
tests/test_workflow_presets.py::test_preset_prompt_files_exist_in_repo PASSED [ 50%]
tests/test_workflow_presets.py::test_get_lane_preset_is_case_insensitive_and_raises_on_unknown PASSED [ 62%]
tests/test_workflow_presets.py::test_guess_lane_preset_matches_exact_sequences_only PASSED [ 75%]
tests/test_workflow_presets.py::test_deep_prompts_are_succinct_and_carry_the_gates PASSED [ 87%]
tests/test_workflow_presets.py::test_deep_plan_prompt_multi_blocker_syntax PASSED [100%]

============================== 8 passed in 0.16s ===============================
```

### 3. Full Suite & Linter Gates
- `ruff check src tests`: Clean exit 0.
- `symphony-pyright`: 0 errors, 0 warnings, 0 informations.
- `git diff --check`: Clean exit 0.
- Full pytest in sanitized environment: 2927 passed, 14 skipped in 201.07s.

## Implementation Notes
- Changed line 19 of `docs/symphony-prompts/file/deep/plan.md` to use repeated `--blocked-by` flags:
  `${SYMPHONY_CLI:-symphony} board new VERIFY-1 "Re-prove all claims" --state Verify --blocked-by BUILD-1 --blocked-by BUILD-2 --request "{{ issue.request }}" --description "..."`
- Extended `test_deep_prompts_are_succinct_and_carry_the_gates` and added `test_deep_plan_prompt_multi_blocker_syntax` in `tests/test_workflow_presets.py`.
- No parser changes made to `src/symphony/cli/board.py` or `src/symphony/trackers/validate.py` (explicitly forbidden by ticket instructions).

## Self-Critique
- **Risks**: None identified; prompt example change matches already-existing parser implementation (`action="append"`).
- **Edge cases**: Multiple blockers with 2+ parents now documented consistently with the underlying argparse action. Single-blocker cases continue to use `--blocked-by <ID>`.
- **Not covered**: Live tracker integration with external web APIs (out of scope).
- **Verify Focus**: Verify prompt succinctness (27 <= 45 lines) and test coverage in `tests/test_workflow_presets.py`.
