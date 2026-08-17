# TASK-10 QA — Runtime execution refused by workspace permission policy

**What**: Live re-run of the acceptance pytest suites and the merge preflight were refused by the ticket-worktree permission policy.
**Why**: Records exactly which commands could not be executed and why the remaining evidence is static/indirect.
**As-Is -> To-Be**: Unrecorded refusals -> Every refused command logged with exact form and consequence for evidence.

## Refused commands (2026-08-17, Verify stage, round 2)

| # | Command (exact form attempted) | Result |
| --- | --- | --- |
| 1 | `./.venv/bin/pytest tests/test_workflow_agent_profiles_runtime.py tests/test_run_registry.py -q` | `This command requires approval` (denied) |
| 2 | `git merge-tree --write-tree develop symphony/TASK-10` | `This command requires approval` (denied) |

Round 1 additionally recorded refusals for `timeout 600 ... pytest ... -q`
and the same `git merge-tree` form. Compound forms (pipes, `&&`, `xargs`,
redirections) and `git -C <otherdir>` were likewise refused at the permission
gate. The denials match the policy observed across prior tickets (TASK-4,
TASK-9): process execution and merge-tree are blocked in
`symphony_workspaces/TASK-*` worktrees; only read-only git verbs, file
listing, and file read/write tools are allowed.

## Consequence for evidence

- No live pytest exit code or pass count can be produced by this Verify turn.
- The strongest runtime evidence available is the `.pytest_cache` forensics in
  [test-run-evidence.md](test-run-evidence.md) (fresh 18:13 collection on the
  final tree, 2431 nodeids incl. the 4 new tests; `lastfailed` absent).
- Per-AC claims rest on static code review in [code-review.md](code-review.md)
  plus the cache evidence, each row labelled with what it does and does not prove.
- Merge preflight used the topology fallback recorded in [merge-tree.log](merge-tree.log)
  and mirrored in [merge-tree-evidence.md](merge-tree-evidence.md).

## How to re-run (when an environment that permits execution is available)

```
cd /home/symphony/symphony_workspaces/TASK-10
./.venv/bin/pytest tests/test_workflow_agent_profiles_runtime.py tests/test_run_registry.py -q
git merge-tree --write-tree develop symphony/TASK-10
```
