# TASK-18 QA — Runtime commands refused by the permission harness

All process-execution attempts during Verify (2026-08-19) were refused with
"This command requires approval". Attempted once per form, per harness policy.

| # | Command (attempted) | Purpose | Result |
|---|---|---|---|
| 1 | `.venv/bin/pytest tests/test_copilot_backend.py -q --no-header -x --tb=short -p no:cacheprovider` | live run of new backend tests | denied |
| 2 | `.venv/bin/python -m py_compile src/symphony/backends/copilot.py src/symphony/backends/pi.py src/symphony/backends/usage.py tests/test_copilot_backend.py` | syntax check of the 4 files edited after the last recorded test run | denied |
| 3 | `.venv/bin/ruff check src/symphony/backends/copilot.py src/symphony/backends/pi.py src/symphony/backends/usage.py` | lint of changed backend files | denied |
| 4 | `git merge-tree --write-tree develop symphony/TASK-18` | merge preflight simulation | denied |
| 5 | `git -C /home/symphony/git/oh-my-symphony status --porcelain` | host worktree dirtiness | denied |

Consequences:

- Live pytest evidence is unavailable in Verify; the `.pytest_cache` from the
  implementation turn is cited as indirect evidence in `qa/pytest-cache.md`.
- Merge preflight relies on the stronger topology proof in `qa/merge-preflight.md`
  (HEAD = develop tip + 1 commit ⇒ pure fast-forward, provably conflict-free).

How to re-run when the harness allows (from the ticket worktree):

```bash
.venv/bin/pytest -q -p no:cacheprovider          # full suite
.venv/bin/pytest tests/test_copilot_backend.py -q -p no:cacheprovider
.venv/bin/ruff check src/symphony/backends/copilot.py src/symphony/backends/pi.py
git merge-tree --write-tree develop symphony/TASK-18
```
