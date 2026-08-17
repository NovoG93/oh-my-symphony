# Repro after — TASK-13 usage-limit test suite (Stage 6.10 + 6.11)

**What**: After-state closure of the reproduction in
`docs/TASK-13/reproduce/eval.md` (68-test usage-limit suite, pyright, ruff).
**Why**: The Verify contract requires the bug reproduction to be closed with
`qa/repro-after.log`; this `.md` is the non-ignored mirror that rides the
Done merge (`*.log` is gitignored).
**As-Is -> To-Be**: Reproduction guide without a recorded after-state ->
Closure log with denial record + durable indirect evidence + re-run commands.

Timestamp: 2026-08-17T20:23:46Z (Document rewind turn). Branch
`symphony/TASK-13`, HEAD `d5b2c3a` — tree is Verify-reviewed `d250c37` plus
the 8 qa evidence files (`git diff d250c37 HEAD` shows only those).

## Live attempts (this session)

| Command | Result |
|---|---|
| `.venv/bin/pytest tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py -q` | Denied: "This command requires approval" |
| `.venv/bin/symphony-pyright` | Denied: "This command requires approval" |

Same permission policy that refused the Verify turn's attempts
(`qa/runtime-blocked.md`); ruff was not re-attempted (same denied
process-execution category).

## Durable indirect evidence (code identical to the completed run)

- `.pytest_cache/v/cache/lastfailed` = `{}` (mtime 2026-08-17T20:11Z): the
  last COMPLETED full-suite pytest session on this branch recorded zero
  failures.
- `.pytest_cache/v/cache/nodeids` (mtime 20:12Z): full-suite collection lists
  all 68 usage-limit ids (27 + 41), matching eval.md's "Expected: 68 passed".
  Full analysis: `qa/pytest-cache-evidence.md`.
- Changed files carry 20:11 mtimes (usage.py, core.py, __init__.py, both
  test files) — the completed run executed exactly the final files.
- `.ruff_cache` (mtime 20:11Z): ruff ran in the same turn.

## Not proven

Fresh exit code / pass count / pyright output / ruff output in this session.

## How to re-run (unrestricted checkout)

```bash
cd /home/symphony/symphony_workspaces/TASK-13
.venv/bin/pytest tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py -q   # expect 68 passed
.venv/bin/symphony-pyright                                                               # expect 0 errors, 0 warnings
.venv/bin/ruff check src/symphony/orchestrator/usage.py src/symphony/orchestrator/__init__.py src/symphony/orchestrator/core.py tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py
.venv/bin/ruff format --check src/symphony/orchestrator/usage.py src/symphony/orchestrator/__init__.py src/symphony/orchestrator/core.py tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py
```

Contract artifact: `qa/repro-after.log` (byte-identical content, gitignored).
