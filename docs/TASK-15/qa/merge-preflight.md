# Merge Preflight: symphony/TASK-15 -> develop (Verify, 2026-08-17)

**What**: Proof that the ticket branch would merge into the target without conflicts.
**Why**: The orchestrator performs the single `--no-ff` merge at Done; Verify proves it is safe beforehand.
**As-Is -> To-Be**: Unverified merge -> Preflight clean via topology proof + host spot-checks.

## Target resolution

- `WORKFLOW.md:304/307` -> `feature_base_branch: "develop"`, `auto_merge_target_branch: "develop"`.
- Host repo `.git/HEAD` (read via Read tool) = `ref: refs/heads/develop`.
- Worktree shared ref `develop` tip = `af8d6858bec1615833d4e545204621f26e5da1bd` (merge of TASK-14).
- All three agree: target = **develop**.

## Prescribed preflight command

`git merge-tree --write-tree develop symphony/TASK-15` — **denied** by the ticket-worktree permission policy (workspace and host `git -C` forms); attempt recorded in `qa/runtime-blocked.md` and `qa/merge-tree.log`.

## Topology substitute (stronger than a conflict report)

- `git merge-base develop HEAD` = `af8d6858bec1615833d4e545204621f26e5da1bd` = develop's tip.
- Therefore develop is a full ancestor of `symphony/TASK-15`; the branch is develop + this ticket's commits. A `--no-ff` merge applies zero target-side deltas to conflict with -> **conflict-free by construction**.

## Host working-tree overlap check

Full `git -C` host status denied (see `qa/runtime-blocked.md`). Substituted spot-checks, all clean:

- Host `src/symphony/backends/usage.py` read via Read tool == `git show develop:src/symphony/backends/usage.py` byte-for-byte (develop's pre-branch version).
- Host `src/symphony/backends/agy.py` == develop's version (probe code absent, as expected — it exists only on the branch).
- Host has no `tests/test_backend_usage_probes.py` (branch-only file) and no `docs/TASK-15/` directory (no overlap with the branch's docs paths).
- Host's mutable areas (`kanban/`, `log/`) are git-ignored (`/kanban/` in `.gitignore`), so board activity cannot dirty tracked files.
- Branch delta paths (`git diff develop...HEAD --name-only`): the 11 paths listed in `qa/static-review.md` — all ticket scope, none overlapping host-mutable files.

## Verdict

Preflight clean: target develop, feature branch symphony/TASK-15; orchestrator will create the single `--no-ff` merge at Done.

**How to re-run** (where execution is allowed): `git merge-tree --write-tree develop symphony/TASK-15` from the host repo; expect empty conflict output.
