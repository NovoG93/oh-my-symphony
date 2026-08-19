# TASK-21 Verify — runtime command refusals (2026-08-19)

**What**: Live pytest and `git merge-tree` invocations refused by the workspace harness permission policy.
**Why**: Explains why full-suite proof uses `.pytest_cache` evidence instead of a fresh run.
**As-Is -> To-Be**: Direct runtime QA attempted -> refusal recorded, indirect evidence used.

## Refusals

| Command | Form | Result |
|---|---|---|
| `.venv/bin/pytest tests/test_copilot_backend.py tests/test_chat.py -k "test_summarize_copilot_frame or test_summarize_frame_dispatches_copilot" -q` | exec from workspace cwd | "This command requires approval" |
| `python3 -c "import json;..."` | interpreter | "This command requires approval" |
| `git merge-tree --write-tree develop symphony/TASK-21` | workspace cwd | "This command requires approval" |
| `awk '{print gsub(/,/, "&")+1}' ...` | workspace cwd | "This command requires approval" |
| compound `&&` / `;` / pipes / writes to `/tmp` | various | denied (sandbox policy) |

**What this does not prove**: that the suite would fail live. The denial is a
harness policy gate (documented in the workspace permission-gates memory), not
a test result. Fresh-run attempts were made once per form, per policy.

**How to re-run** (in an unrestricted environment):
```bash
.venv/bin/pytest -q                         # AC4 full suite
.venv/bin/pytest tests/test_copilot_backend.py tests/test_chat.py \
    -k "test_summarize_copilot_frame or test_summarize_frame_dispatches_copilot" -q
git merge-tree --write-tree develop symphony/TASK-21
```
