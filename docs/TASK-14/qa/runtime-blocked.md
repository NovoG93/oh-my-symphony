# TASK-14 QA — Runtime execution refusals

**What**: Record of every runtime command form attempted during Verify and the harness denial for each.
**Why**: Runtime QA depends on executing pytest; the harness permission policy denied all execution forms, so evidence must come from durable static artefacts plus the recorded denials.
**As-Is -> To-Be**: As-Is: no record of what was attempted. To-Be: each attempted form, its denial message, and the resulting evidence fallback are on record.

## Denied command forms (2026-08-17, Verify stage)

| # | Command form | Denial message |
|---|---|---|
| 1 | `/home/symphony/symphony_workspaces/TASK-14/.venv/bin/pytest tests/test_codex_usage.py -q 2>&1 \| tail -30` | "This Bash command contains multiple operations. The following part requires approval" |
| 2 | `/home/symphony/symphony_workspaces/TASK-14/.venv/bin/pytest tests/test_codex_usage.py -q` | "This command requires approval" |
| 3 | `python3 -m pytest tests/test_codex_usage.py -q` | "This command requires approval" |
| 4 | `uv run pytest tests/test_codex_usage.py -q` | "This command requires approval" |
| 5 | `git merge-tree --write-tree develop symphony/TASK-14` | "This command requires approval" |
| 6 | `git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree develop symphony/TASK-14` | "This command requires approval" |

Each form was attempted exactly once, per the workspace permission-gate playbook.

## Fallback evidence used

- **Test results**: `.pytest_cache/v/cache/` (`nodeids`, `lastfailed`) written by the implementation run at 20:37 UTC, minutes before the `wip` commit — see `qa/test-cache-evidence.md`.
- **Merge preflight**: read-only git verbs (`git merge-base`, `git diff --name-only`, `git rev-parse`) plus the host repo's `.git/HEAD` and `.git/refs/heads/develop` — see `qa/merge-preflight.md` and `qa/merge-tree.log`.

## What this does not prove

A live pytest run under the current session's environment. The cache evidence proves the last recorded run; see `qa/test-cache-evidence.md` for the exact claim boundaries.
