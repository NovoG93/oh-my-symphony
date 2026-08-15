# TASK-5 Merge Preflight (Verify re-pass 2026-08-15T20:02Z)

Durable mirror of `qa/merge-tree.log` (that path is gitignored via `*.log`).

## Command attempted

`git merge-tree --write-tree main symphony/TASK-5` — DENIED by the workspace
permission policy ("This command requires approval"; one attempt, not retried).

## Target resolution

- Branch WORKFLOW.md lines 301/304: `feature_base_branch: "main"`,
  `auto_merge_target_branch: "main"`.
- `git show main:WORKFLOW.md` lines 301/304: same values.
- Host repo HEAD (Read on `/home/symphony/git/oh-my-symphony/.git/HEAD`):
  `ref: refs/heads/main`.
- Effective target: **main**.

## Static conflict analysis (post-rewind)

- `git merge-base main symphony/TASK-5` -> `6d75be5` (TASK-4 merge commit).
- `git diff 6d75be5 HEAD --stat` (branch side) -> 25 files under
  `src/symphony/`, `tests/`, `docs/TASK-5/` — **no `graphify-out`** (rewind
  fix verified: `git show HEAD:graphify-out` -> fatal; removal commit 390edf2).
- `git diff 6d75be5 main --stat` (main side) -> 2 files: `WORKFLOW.md`,
  `docs/symphony-prompts/file/base.md`.
- Intersection of the two file sets: **empty** -> no textual conflict possible
  in the three-way merge.

## What this proves / does not prove

- Proves: the branch and main diverge only on disjoint paths; a merge cannot
  hit content conflicts (rename/type-change edge cases are impossible here —
  neither side renamed/deleted shared files). Also proves the orphan-scope
  `graphify-out` symlink finding from the first Verify pass is resolved.
- Does not prove: byte-level `git merge-tree` output (command denied), and
  host-repo dirty tracked files were not checkable from this session
  (`git -C` denied); the host-managed `kanban/` dir is gitignored on both
  sides so it cannot collide with branch content.

## Verdict

Preflight structurally clean; the orchestrator will create the single
`--no-ff` merge commit at Done.

How to re-run (when execution is permitted):
`cd /home/symphony/symphony_workspaces/TASK-5 && git merge-tree --write-tree main symphony/TASK-5`
