# Merge Preflight: symphony/TASK-17 -> develop (Verify, 2026-08-17)

**What**: Proof that the ticket branch would merge into the target without conflicts.
**Why**: The orchestrator performs the single `--no-ff` merge at Done; Verify proves it is safe beforehand.
**As-Is -> To-Be**: Unverified merge -> Preflight clean via topology proof + delta check.

(Note: the prescribed log file `qa/merge-tree.log` is git-ignored by `*.log`; this `.md` is its durable mirror.)

## Target resolution

- `WORKFLOW.md:304/307` -> `feature_base_branch: "develop"`, `auto_merge_target_branch: "develop"`.
- Host repo `.git/HEAD` (read via Read tool) = `ref: refs/heads/develop`.
- Worktree shared ref `develop` tip = `115223c1ac0e10a4fcfb6d3135431deb3691a72e` (merge of TASK-16).
- All three agree: target = **develop**.

## Prescribed preflight command

`git merge-tree --write-tree develop symphony/TASK-17` — **denied** by the ticket-worktree permission policy; attempt recorded in `qa/runtime-blocked.md` and `qa/merge-tree.log`.

## Topology substitute (stronger than a conflict report)

- `git merge-base develop symphony/TASK-17` = `115223c1ac0e10a4fcfb6d3135431deb3691a72e` = develop's tip.
- Therefore develop is a full ancestor of `symphony/TASK-17`; the branch is develop + this ticket's single wip commit (`f44482b`). A `--no-ff` merge applies zero target-side deltas to conflict with -> **conflict-free by construction**.

## Host working-tree overlap check

- Branch delta paths (`git diff --name-only develop..HEAD`): 7 paths — README.md, WORKFLOW.example.md, WORKFLOW.file.example.md, docs/features/agent-profiles.md, tests/test_usage_limits.py, docs/TASK-17/work/{details,plan}.md. All ticket scope.
- Host-mutable areas (`kanban/`, `*.log`) are git-ignored (`.gitignore:28,30`), so host board activity cannot dirty tracked files that overlap the delta.
- Worktree itself is clean (`git status` at Verify start: nothing to commit).

## Verdict

Preflight clean: target develop, feature branch symphony/TASK-17; orchestrator will create the single `--no-ff` merge at Done.

**How to re-run** (where execution is allowed): `git merge-tree --write-tree develop symphony/TASK-17` from the host repo; expect exit 0, no conflict lines.
