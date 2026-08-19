# TASK-20 QA — Merge preflight (mirror of merge-tree.log, which is gitignored)

**What**: Preflight proving `symphony/TASK-20` would merge into `develop` without conflicts.
**Why**: The orchestrator creates the single `--no-ff` merge at Done; Verify must prove the branch is merge-safe first.
**As-Is -> To-Be**:
- As-Is: `git merge-tree --write-tree develop symphony/TASK-20` denied by harness policy.
- To-Be: Read-only topology proof used instead.

## Command + result

```bash
git merge-tree --write-tree develop symphony/TASK-20
# DENIED by harness permission policy ("This command requires approval") — no output.
```

## Fallback topology proof (all read-only verbs, run 2026-08-19)

```bash
git rev-parse develop symphony/TASK-20
# 3a436bb1b6b9410526cc10c4e5848ec71a0cedc9   develop tip
# 82ff2033d7f58754f64f6afa7ff5f61dedaf6a83   symphony/TASK-20 tip

git merge-base develop symphony/TASK-20
# 3a436bb1b6b9410526cc10c4e5848ec71a0cedc9
```

`merge-base == develop tip` → branch is exactly one commit ahead of develop, zero divergence → a three-way merge applies only the branch's own delta → no conflicted paths.

## Overlap check

- Branch delta: `docs/TASK-20/work/details.md`, `src/symphony/backends/copilot.py`, `tests/test_copilot_backend.py`, `tests/test_orchestrator_usage_limits.py`.
- Workspace dirty tracked files: none (`git status` clean) → no overlap.

## Target resolution

- `WORKFLOW.md:304` `feature_base_branch: "develop"`; `WORKFLOW.md:307` `auto_merge_target_branch: "develop"`; host repo HEAD = `ref: refs/heads/develop`.

## What this proves / does not prove

- Proves: zero-divergence topology (no possible textual conflict given develop has not moved) and a clean working tree.
- Does not prove: the byte-level merge result a real `merge-tree --write-tree` would print (denied here). Re-run it in an unrestricted environment:

```bash
cd /home/symphony/symphony_workspaces/TASK-20
git merge-tree --write-tree develop symphony/TASK-20
```

## Document-stage re-check (2026-08-19, state Document)

- Branch tip moved `82ff203` -> `29527c2` (harness auto-commit; same message
  `wip: turn 2026-08-19T17:26:17Z`). `git diff 82ff203 29527c2` shows only the
  5 `docs/TASK-20/qa/*.md` files added — the source/test tree is identical, so
  the Verify-stage review and cache evidence still describe the current tip.
- Topology re-verified at Document: `git rev-parse develop symphony/TASK-20` ->
  `3a436bb…` / `29527c2…`; `git merge-base develop symphony/TASK-20` ->
  `3a436bb…` (== develop tip). Still exactly one commit ahead, zero divergence.
- `git status` clean (no dirty tracked files) — preflight conclusion unchanged.
