# TASK-19 Verify — runtime commands blocked by harness permission policy

Attempted once each on 2026-08-19 during Verify; every process-executing
command was refused by the workspace permission policy ("This command requires
approval"). No retries of the same form were attempted.

| # | Command (from worktree `/home/symphony/symphony_workspaces/TASK-19`) | Outcome |
|---|---|---|
| 1 | `.venv/bin/pytest tests/test_copilot_backend.py -q` | denied |
| 2 | `.venv/bin/pyright src/symphony/backends/copilot.py` | denied |
| 3 | `.venv/bin/ruff check src/symphony/backends/copilot.py tests/test_copilot_backend.py` | denied |
| 4 | `git merge-tree --write-tree develop symphony/TASK-19` (from host repo `/home/symphony/git/oh-my-symphony`) | denied |

Allowed and executed:
- `python3 --version` -> `Python 3.14.4`
- Read-only git verbs (`git log`, `git show`, `git diff`, `git rev-parse`,
  `git merge-base`, `git status --short`) — see `qa/merge-tree.log`.

Consequence for QA: live pytest/pyright/ruff results are **Not proven** this
pass. Indirect evidence of the most recent recorded pytest session is analysed
in `qa/pytest-cache-evidence.md`.

How to re-run (when the permission policy allows, or in CI):
```
cd /home/symphony/symphony_workspaces/TASK-19
.venv/bin/pytest tests/test_copilot_backend.py -q
.venv/bin/pytest -q
.venv/bin/python -m ruff check src tests
symphony-pyright
```

## Pass 2 attempts (2026-08-19, post-fix HEAD c01fc45)

Same policy result; one attempt per form, no retries:

| # | Command (from worktree unless noted) | Outcome |
|---|---|---|
| 5 | `.venv/bin/pytest tests/test_copilot_backend.py -q` | denied |
| 6 | `.venv/bin/python -m ruff check src tests` | denied |
| 7 | `.venv/bin/pyright --pythonpath .venv/bin/python src/ tests/test_copilot_backend.py` | denied |
| 8 | `git merge-tree --write-tree develop symphony/TASK-19` (host repo cwd) | denied |
