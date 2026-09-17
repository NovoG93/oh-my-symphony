# QA Verification Details: TIER-002

## Overview
- **Ticket**: TIER-002 (Fix invalid deep-planner multi-blocker syntax)
- **State**: Verify -> Document
- **Target Branch**: `develop`
- **Feature Branch**: `symphony/TIER-002`
- **Scope**: `docs/symphony-prompts/file/deep/plan.md` and `tests/test_workflow_presets.py`

---

## Review
### Code Diff and Requirement Alignment
- Verified full git diff against ticket requirements, `## Plan`, `## Acceptance Tests`, and `## Done Signals`.
- **Target File**: `docs/symphony-prompts/file/deep/plan.md:19`
  - Before: `${SYMPHONY_CLI:-symphony} board new VERIFY-1 "Re-prove all claims" --state Verify --blocked-by BUILD-1,BUILD-2 --request "{{ issue.request }}" --description "..."`
  - After: `${SYMPHONY_CLI:-symphony} board new VERIFY-1 "Re-prove all claims" --state Verify --blocked-by BUILD-1 --blocked-by BUILD-2 --request "{{ issue.request }}" --description "..."`
- **Test File**: `tests/test_workflow_presets.py:97-98,140-149`
  - Added assertions ensuring `--blocked-by BUILD-1 --blocked-by BUILD-2` is in `plan.md` and `--blocked-by BUILD-1,BUILD-2` is not in `plan.md`.
  - Added dedicated test `test_deep_plan_prompt_multi_blocker_syntax`.
- **Succinctness Budget**:
  - `docs/symphony-prompts/file/deep/plan.md` is 27 lines (line count <= 45 budget).
- **Scope Boundaries**:
  - Zero modifications to CLI parsing logic (`src/symphony/cli/board.py` left untouched).
  - Zero modifications to tracker validation logic (`src/symphony/trackers/validate.py` left untouched).
  - No orphan scope or unrelated edits.

---

## Security Audit
See [security-audit.md](file:///home/symphony/symphony_workspaces/TIER-002/docs/TIER-002/qa/security-audit.md) for full rationale.
1. `secrets`: `pass` — Zero credentials or secrets in diff or commits; verified in sanitized environment.
2. `input-validation`: `pass` — Replaces invalid comma format with valid repeated CLI flags adhering to whitelist identifier regex.
3. `injection`: `pass` — Discrete arguments prevent shell delimiter and argument injection.
4. `xss`: `n/a` — Non-UI prompt markdown change; no web rendering.
5. `csrf`: `n/a` — No web endpoints or session state modified.
6. `authz`: `n/a` — No authorization rules or permissions altered.
7. `rate-limit`: `n/a` — No network API calls involved.

---

## QA Evidence
### Command Manifest

#### 1. Targeted Preset Tests
- **Command**: `.venv/bin/python -m pytest -v tests/test_workflow_presets.py`
- **Exit Code**: 0
- **Evidence Path**: `docs/TIER-002/qa/test-results.log`
- **What it proves**: All 8 preset workflow tests pass, specifically verifying prompt succinctness and the repeated `--blocked-by` syntax requirement.
- **What it does not prove**: Live ticket creation on external trackers.
- **How to re-run**: `.venv/bin/python -m pytest -v tests/test_workflow_presets.py`

#### 2. Linter Gate (Ruff)
- **Command**: `.venv/bin/ruff check src tests`
- **Exit Code**: 0
- **Evidence Path**: `docs/TIER-002/qa/lint-typecheck.log`
- **What it proves**: Source code and tests comply with all linter rules.
- **What it does not prove**: Functional runtime correctness.
- **How to re-run**: `.venv/bin/ruff check src tests`

#### 3. Type Checker (Pyright)
- **Command**: `.venv/bin/symphony-pyright`
- **Exit Code**: 0
- **Evidence Path**: `docs/TIER-002/qa/lint-typecheck.log`
- **What it proves**: 0 errors, 0 warnings across `src`.
- **What it does not prove**: Dynamically typed prompt template syntax.
- **How to re-run**: `.venv/bin/symphony-pyright`

#### 4. Whitespace & Diff Check
- **Command**: `git diff --check develop..symphony/TIER-002`
- **Exit Code**: 0
- **Evidence Path**: `docs/TIER-002/qa/lint-typecheck.log`
- **What it proves**: Diff has no trailing whitespace or patch formatting errors.
- **What it does not prove**: Semantic code correctness.
- **How to re-run**: `git diff --check develop..symphony/TIER-002`

#### 5. Full Test Suite (Sanitized Environment)
- **Command**: `env -u SYMPHONY_API_AUTH_MODE -u SYMPHONY_API_TOKEN_FILE -u SYMPHONY_API_TOKEN -u SYMPHONY_TRUSTED_ORIGINS -u SYMPHONY_REMOTE_OPERATOR_CAPABILITIES .venv/bin/python -m pytest -q`
- **Exit Code**: 0
- **Evidence Path**: `docs/TIER-002/qa/full-suite.log`
- **What it proves**: Complete suite passes with 2927 passed, 14 skipped in clean auth-free environment.
- **What it does not prove**: Browser E2E suite (marked `browser_e2e`, requires live browser binaries).
- **How to re-run**: `env -u SYMPHONY_API_AUTH_MODE -u SYMPHONY_API_TOKEN_FILE -u SYMPHONY_API_TOKEN -u SYMPHONY_TRUSTED_ORIGINS -u SYMPHONY_REMOTE_OPERATOR_CAPABILITIES .venv/bin/python -m pytest -q`

#### 6. End-to-End Defect Reproduction Loop
- **Command**: `.venv/bin/python /home/symphony/.gemini/antigravity-cli/brain/4857ce5b-737e-4f4e-bf4f-db0f9ac3385a/scratch/verify_repro.py`
- **Exit Code**: 0
- **Evidence Path**: `docs/TIER-002/qa/repro-after.log`
- **What it proves**: Confirms the previous syntax fails validation with `BoardDependencyError`, while the corrected repeated-flag syntax creates child tickets with both blockers cleanly stored in frontmatter.
- **What it does not prove**: Production execution with multi-agent orchestration.
- **How to re-run**: `.venv/bin/python /home/symphony/.gemini/antigravity-cli/brain/4857ce5b-737e-4f4e-bf4f-db0f9ac3385a/scratch/verify_repro.py`

---

## AC Scorecard

| Acceptance Criterion | Signal | Source | Result | Evidence Path |
|---|---|---|---|---|
| Deep Plan prompt contains repeated `--blocked-by` flags | `--blocked-by BUILD-1 --blocked-by BUILD-2` present in prompt line 19 | `docs/symphony-prompts/file/deep/plan.md` | PASS | `qa/repro-after.log` |
| Deep Plan prompt does not contain comma-separated syntax | `--blocked-by BUILD-1,BUILD-2` absent from prompt | `docs/symphony-prompts/file/deep/plan.md` | PASS | `qa/repro-after.log` |
| Regression test proves exact supported syntax | 2 targeted tests pass asserting repeated flags and forbidding commas | `tests/test_workflow_presets.py` | PASS | `qa/test-results.log` |
| No files outside the prompt and its focused test change | Diff touches only `docs/symphony-prompts/file/deep/plan.md` and `tests/test_workflow_presets.py` (and ticket evidence under `docs/TIER-002/`) | `git diff develop..symphony/TIER-002` | PASS | `qa/lint-typecheck.log` |
| All verification gates pass and evidence is committed without secrets | Ruff, Pyright, full pytest, and merge-tree preflight pass clean | CI & Quality Gates | PASS | `qa/full-suite.log`, `qa/merge-tree.log` |

---

## Merge Preflight
- **Target Branch**: `develop` (resolved via `agent.auto_merge_target_branch`)
- **Feature Branch**: `symphony/TIER-002`
- **Host Repo**: `/home/symphony/git/oh-my-symphony`
- **Preflight Command**: `git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree develop symphony/TIER-002`
- **Exit Code**: 0
- **Output Tree Hash**: `a2d38e603956d0280486267822186425c8554907`
- **Host Repo Status**: Working tree clean; zero conflicting tracked files.
- **Preflight Status**: Preflight clean, orchestrator will merge at Done.
