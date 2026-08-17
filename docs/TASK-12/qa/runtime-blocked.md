# TASK-12 Verify: Runtime Commands Blocked by Permission Policy

All process/network execution is denied in this workspace ("This command requires approval").
Each form was attempted once, per workspace policy. Denied commands and their purpose:

| # | Command (attempted 2026-08-17) | Purpose |
|---|---|---|
| 1 | `.venv/bin/python -m pytest tests/test_usage_limits.py tests/test_workflow_agent_profiles.py -q` | Stage 6.1 test run (AC7) |
| 2 | `pytest tests/test_usage_limits.py tests/test_workflow_agent_profiles.py -q` | same, alternate entry |
| 3 | `.venv/bin/pyright src tests` | type-check Done Signal (0 errors / 0 warnings) |
| 4 | `git merge-tree --write-tree develop symphony/TASK-12` | merge preflight (see `qa/merge-tree.md`) |
| 5 | `git check-ignore -v docs/TASK-12/qa/merge-tree.log` | confirm .log ignore rule |

Also blocked: Read of the authoritative spec `/home/symphony/usage-aware-agent-profiles-plan.md`
(outside permitted directories); conformance anchored on ticket ACs + `work/stage-1-model-and-validation.md`.

Consequence: fresh runtime proof is **Not proven** in this session. Indirect evidence:
- pytest cache analysis: `qa/pytest-cache-evidence.md`
- recorded implementation runs: `docs/TASK-12/work/details.md` (30 + 16 passed; full suite
  2451 passed, 9 skipped; pyright 0/0) — recorded by the implementation agent, not re-run here.
- static review of all validation and typing logic: `qa/static-validation-review.md`

## How to re-run (reviewer, once exec is permitted)
```
cd /home/symphony/symphony_workspaces/TASK-12
.venv/bin/python -m pytest tests/test_usage_limits.py tests/test_workflow_agent_profiles.py -q
.venv/bin/pyright src tests
.venv/bin/python -m pytest -q
```
