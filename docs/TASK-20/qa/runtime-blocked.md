# TASK-20 QA — Runtime execution blocked by harness permission policy

**What**: All process/network commands are denied in this ticket worktree; QA uses static checks and the `.pytest_cache` execution trail instead.
**Why**: Harness policy (consistent across TASK-1..TASK-19 stages) refuses `pytest`, `python`, `pip`, `curl`, and `git merge-tree` ("This command requires approval").
**As-Is -> To-Be**:
- As-Is: Live acceptance runs impossible in this environment.
- To-Be: Each acceptance command attempted once, refusal recorded, indirect cache-based evidence documented with exact re-run commands for an unrestricted environment.

## Denial record (attempted 2026-08-19, Verify stage)

| Command | Result |
|---|---|
| `pytest tests/test_copilot_backend.py tests/test_orchestrator_usage_limits.py -q` | denied: "This command requires approval" |
| `pytest tests/test_copilot_backend.py -k test_remaining_percentage_converts_to_used_percentage -q` | denied |
| `pytest tests/test_copilot_backend.py -k test_copilot_quota_probe_failure_fails_open -q` | denied |
| `pytest tests/test_copilot_backend.py -k test_genuine_copilot_credit_exhaustion -q` | denied |
| `pytest tests/test_copilot_backend.py -k test_generic_rate_limit_does_not_mark_plan_exhausted -q` | denied |
| `git merge-tree --write-tree develop symphony/TASK-20` | denied |

## How to re-run (unrestricted environment)

```bash
cd /home/symphony/symphony_workspaces/TASK-20
pytest tests/test_copilot_backend.py tests/test_orchestrator_usage_limits.py -q
pytest tests/test_copilot_backend.py -k test_remaining_percentage_converts_to_used_percentage -q
pytest tests/test_copilot_backend.py -k test_copilot_quota_probe_failure_fails_open -q
pytest tests/test_copilot_backend.py -k test_genuine_copilot_credit_exhaustion -q
pytest tests/test_copilot_backend.py -k test_generic_rate_limit_does_not_mark_plan_exhausted -q
```

## What this proves / does not prove

- Proves: every declared acceptance command was attempted once and refused by the harness; none was silently skipped.
- Does not prove: live exit codes in this environment. Live-execution evidence is supplied indirectly by `qa/pytest-cache-evidence.md`.
