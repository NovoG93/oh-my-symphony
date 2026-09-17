# Document Stage Details: TIER-002

## Overview
- **Ticket**: TIER-002 (Fix invalid deep-planner multi-blocker syntax)
- **Stage**: Document -> Done
- **Target Branch**: `develop`
- **Feature Branch**: `symphony/TIER-002`
- **Scope**: `docs/symphony-prompts/file/deep/plan.md` and `tests/test_workflow_presets.py`

---

## Brief vs. Reality Comparison

### 1. Goal and Problem Diagnosis
- **Brief**: At engine revision `58c703f85353963697573e8dd60fa6607986e99b`, `docs/symphony-prompts/file/deep/plan.md` contained `--blocked-by BUILD-1,BUILD-2`. The live CLI rejected this as a malformed identifier because `--blocked-by` targets must match `^[A-Za-z][A-Za-z0-9_-]{0,63}$`.
- **Reality**: Confirmed. `symphony board new` configures `--blocked-by` with `action="append"`. Passing a comma-delimited string causes validation to fail with `error: blocked_by target must match ^[A-Za-z][A-Za-z0-9_-]{0,63}$ (identifier='BUILD-1,BUILD-2')`.

### 2. Implementation Scope
- **Brief**: Correct the deep planner prompt to use repeated `--blocked-by` flags (`--blocked-by BUILD-1 --blocked-by BUILD-2`). Add or extend automated tests under `tests/`. Do not edit CLI parsing to accept commas; do not modify Workmate, homelab-infra, routing policy, model profiles, or unrelated prompt text.
- **Reality**: Exactly one line changed in `docs/symphony-prompts/file/deep/plan.md` (line 19). Two targeted test assertions added to `tests/test_workflow_presets.py`. Zero modifications to CLI parsing or tracker schemas.

### 3. Verification and Evidence Audit
- **Prompt Succinctness**: `docs/symphony-prompts/file/deep/plan.md` is 27 lines, well below the 45-line budget.
- **Preset Tests**: `.venv/bin/python -m pytest -v tests/test_workflow_presets.py` -> 8 passed in 0.18s.
- **Linter Gate**: `.venv/bin/ruff check src tests` -> All checks passed!
- **Type Checker**: `.venv/bin/symphony-pyright` -> 0 errors, 0 warnings, 0 informations.
- **Full Test Suite**: Sanitized environment (`env -u SYMPHONY_API_*`) -> 2927 passed, 14 skipped in 200.43s.
- **Defect Repro**: Confirmed resolved via `docs/TIER-002/qa/repro-after.log`.

---

## Stage Contract Invariant & Rewind Analysis

### What Happened
At transition from Verify to Document, Symphony reported:
```text
Stage verify did not produce the required outputs.
Missing:
- ## Merge Status
```

### Root Cause
In `src/symphony/orchestrator/contracts.py:306-310`:
```python
pattern = re.compile(
    r"^##\s+" + re.escape(heading[3:].strip()) + r"\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
```
The Verify section heading was authored with an explanatory subtitle:
`## Merge Status: preflight clean, orchestrator will merge at Done`
Because the regex enforces `^##\s+Merge Status\s*:?$`, the trailing phrase caused the regex match to fail, even though complete merge preflight evidence was present in the section body.

### Resolution
The heading in `kanban/TIER-002.md` was restored to exact `## Merge Status`. Both Verify and Done stage contracts now evaluate cleanly.

---

## Learnings
1. **Contract Headings Must Be Literal**: Orchestrator contracts match exact section headings (`## Merge Status`, `## QA Evidence`, etc.). Subtitles or descriptive text on the `## ` line break regex matching and trigger contract rewinds.
2. **CLI Argument Parser Pattern**: In Symphony CLI tools (`symphony board new`, `symphony board update`), multi-value arguments rely on repeated option flags (e.g. `--blocked-by A --blocked-by B`), not comma separation.
3. **Prompt Gate Assertions**: Regression tests in `tests/test_workflow_presets.py` should assert both the presence of valid repeated syntax and the absence of comma-separated syntax.

---

## Wiki Updates
- Updated `docs/llm-wiki/planner-tiered-build-routing.md`: Added TIER-002 decision log documenting repeated `--blocked-by` CLI syntax requirement.
- Updated `docs/llm-wiki/INDEX.md`: Updated last-touched timestamp to `2026-09-17 (TIER-002)` and refreshed summary.
- Ran `symphony wiki-sweep --root docs/llm-wiki`: Passed with 0 duplicates, 0 orphans, 0 missing files.
