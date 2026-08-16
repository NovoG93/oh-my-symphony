# TASK-7 Verify — Merge Preflight Record

## Target resolution

- `WORKFLOW.md` line 301: `agent.feature_base_branch: "main"`
- `WORKFLOW.md` line 304: `agent.auto_merge_target_branch: "main"`
- Host repo HEAD (`/home/symphony/git/oh-my-symphony/.git/HEAD`, read via
  Read tool): `ref: refs/heads/main` — host is on `main`.
- **Target branch: `main`.** Feature branch: `symphony/TASK-7` @ `4c7b7e0`
  (`wip: turn 2026-08-15T21:23:31Z`).

## Preflight command

`git merge-tree --write-tree main symphony/TASK-7` — refused by the workspace
permission gate, both from the workspace cwd and via
`git -C /home/symphony/git/oh-my-symphony` (attempted once per form; see
`qa/runtime-blocked.md`). The prescribed log path `qa/merge-tree.log` carries
this refusal record; this `.md` is its durable mirror (`*.log` is gitignored).

## Substitute proof — clean by topology

- `git merge-base main HEAD` → `9fb8695d3d50c2dfcecf84386f942807698e2794`
- `git rev-parse main` → `9fb8695d3d50c2dfcecf84386f942807698e2794`
- main's tip **is** the merge-base, i.e. `symphony/TASK-7` is a strict linear
  descendant of `main`. A merge of a linear descendant cannot conflict — the
  merge-tree result would be the branch tree itself.
- `git diff --name-only main...HEAD` → 15 paths, enumerated in
  `qa/static-review.md`: 14 in ticket scope (src/tests/docs) + the LOW-1
  `graphify-out` symlink.
- Working tree clean (`git status`), so the branch tip equals the worktree.

## Host dirty-tracked-files check

Not executable from the worktree (git writes/`-C` denied). Host HEAD is
`main`; the host working tree is managed by the orchestrator. No overlapping
paths are expected beyond the 15 branch paths; flagged as a limitation, not a
conflict.

## Verdict

Preflight clean (topology-proven): orchestrator creates the single `--no-ff`
merge commit when the ticket reaches Done. Recommend excluding the
`graphify-out` symlink from the merge (LOW-1 in `qa/static-review.md`).

How to re-run on an unrestricted machine:
`git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree main symphony/TASK-7`
