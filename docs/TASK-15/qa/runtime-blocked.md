# Runtime-Blocked: denied command forms (Verify, 2026-08-17)

**What**: Live runtime verification commands refused by the ticket-worktree permission policy.
**Why**: Documents why QA relies on recorded-run + static evidence instead of a fresh pytest run.
**As-Is -> To-Be**: No record -> Every denied form logged with the fallback evidence used.

Denied forms (each attempted exactly once, response "This command requires approval"):

| # | Command | Purpose | Fallback evidence |
|---|---|---|---|
| 1 | `.venv/bin/python -m pytest tests/test_backend_usage_probes.py -q --no-header -p no:cacheprovider` | Fresh run of the 36 acceptance tests | `qa/test-cache-evidence.md` |
| 2 | `.venv/bin/pytest tests/test_backend_usage_probes.py -q --no-header -p no:cacheprovider` | Same, binary form | `qa/test-cache-evidence.md` |
| 3 | `uv run pytest tests/test_backend_usage_probes.py -q --no-header -p no:cacheprovider` | Same, uv form | `qa/test-cache-evidence.md` |
| 4 | `git merge-tree --write-tree develop symphony/TASK-15` | Prescribed merge preflight | `qa/merge-preflight.md`, `qa/merge-tree.log` |
| 5 | `git -C /home/symphony/git/oh-my-symphony status --porcelain` | Host dirty-tracked-files check | Host spot-checks in `qa/merge-preflight.md` |
| 6 | `git hash-object /home/symphony/git/oh-my-symphony/src/symphony/backends/usage.py` | Byte-identity of host file vs develop blob | Read-tool spot-check in `qa/merge-preflight.md` |

This matches the standing permission policy recorded for `symphony_workspaces/TASK-*`
worktrees: all process/network execution and write-side git verbs are denied;
read-only git verbs and file Read/Write remain allowed.

**How to re-run** (in an unrestricted environment, e.g. the host repo checkout):

```bash
cd /home/symphony/git/oh-my-symphony
git worktree add /tmp/task15-verify symphony/TASK-15
cd /tmp/task15-verify
python -m pytest tests/test_backend_usage_probes.py -q
python -m pytest tests/test_backend_usage_probes.py tests/test_codex_usage.py tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py -q
python -m pytest -q            # full suite
git merge-tree --write-tree develop symphony/TASK-15
```
