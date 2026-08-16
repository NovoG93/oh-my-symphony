# TASK-8 Verify — Merge Preflight Record

## Target resolution

- `WORKFLOW.md` line 301: `agent.feature_base_branch: "main"`
- `WORKFLOW.md` line 304: `agent.auto_merge_target_branch: "main"`
- Host repo HEAD (`/home/symphony/git/oh-my-symphony/.git/HEAD`, read via
  Read tool): `ref: refs/heads/main` — host is on `main`.
- **Target branch: `main`.** Feature branch: `symphony/TASK-8` @ `95f3aec`
  (`wip: turn 2026-08-15T21:50:13Z`).

## Preflight command

`git merge-tree --write-tree main symphony/TASK-8` — refused by the workspace
permission gate, both from the workspace cwd and via
`git -C /home/symphony/git/oh-my-symphony` (attempted once per form; see
`qa/runtime-blocked.md`). The prescribed log path `qa/merge-tree.log` carries
this refusal record; this `.md` is its durable mirror (`*.log` is gitignored).

## Substitute proof — clean by topology

- `git merge-base main HEAD` → `423198971116e1d7c8999957ce7fbdc0ca88ce6a`
- `git rev-parse main` → `423198971116e1d7c8999957ce7fbdc0ca88ce6a`
- main's tip **is** the merge-base, i.e. `symphony/TASK-8` is a strict linear
  descendant of `main`. A merge of a linear descendant cannot conflict — the
  merge-tree result would be the branch tree itself.
- `git diff --name-only main...HEAD` → 8 paths, enumerated in
  `qa/static-review.md`: all in ticket scope (2 READMEs, 1 example file,
  1 feature doc, 2 wiki files, 1 work note, 1 test file). No symlinks, no
  files outside ticket scope (unlike TASK-7's `graphify-out` LOW-1).
- Working tree clean at preflight time (`git status`).

## Host dirty-tracked-files check

Not executable from the worktree (git writes/`-C` denied). Host HEAD is
`main`; the host working tree is managed by the orchestrator. No overlapping
paths are expected beyond the 8 branch paths; flagged as a limitation, not a
conflict.

## Verdict

Preflight clean (topology-proven): orchestrator creates the single `--no-ff`
merge commit when the ticket reaches Done.

How to re-run on an unrestricted machine:
`git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree main symphony/TASK-8`
