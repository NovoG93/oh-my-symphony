# Runtime execution attempts — TASK-13 Verify (2026-08-17)

## What was attempted

| # | Command | Result |
|---|---|---|
| 1 | `.venv/bin/pytest tests/test_usage_limits.py -q 2>&1 \| tail -5` | Denied: "contains multiple operations" (pipe) |
| 2 | `.venv/bin/pytest tests/test_usage_limits.py -q` | Denied: "This command requires approval" |
| 3 | `git merge-tree --write-tree develop symphony/TASK-13` | Denied: "This command requires approval" |

Per the workspace permission policy observed across TASK-1..TASK-12, process
execution (pytest, pyright, ruff, python -m) and git merge-tree are refused in
ticket worktrees. Each form was attempted at most once; no further attempts
were burned.

## What this means

- A fresh green pytest run in THIS Verify session is **Not proven**.
- It is not disproven either: the worktree carries `.pytest_cache` from a
  completed full-suite run at 20:11:xxZ on 2026-08-17 (end of the In Progress
  turn), see `qa/pytest-cache-evidence.md`.

## How to re-run (unrestricted checkout)

```bash
cd /home/symphony/symphony_workspaces/TASK-13
.venv/bin/pytest tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py -q
.venv/bin/symphony-pyright
.venv/bin/ruff check src/symphony/orchestrator/usage.py src/symphony/orchestrator/__init__.py src/symphony/orchestrator/core.py tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py
.venv/bin/ruff format --check src/symphony/orchestrator/usage.py src/symphony/orchestrator/__init__.py src/symphony/orchestrator/core.py tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py
```
