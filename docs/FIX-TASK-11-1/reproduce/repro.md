# Repro Evidence: FIX-TASK-11-1

## Repro Command
```bash
git -C /home/symphony/git/oh-my-symphony diff --name-only
```

## Before State (Reproduction)
When `TASK-11` finished its execution and attempted to auto-merge into `develop`, `TASK-10` had just merged to `develop` at 18:30:33 on the host repository `/home/symphony/git/oh-my-symphony`. Uncommitted tracked modifications in `docs/llm-wiki/INDEX.md` collided with the branch modifications in `symphony/TASK-11`.

When Symphony evaluated `_build_merge_safety_block` in `src/symphony/utils/auto_merge.py`, it detected `docs/llm-wiki/INDEX.md` in the dirty manifest while overlapping with `develop..symphony/TASK-11`. The merge safety check exited with status 41 (`_RC_SKIP_DIRTY`), causing the orchestrator to mark `TASK-11` as `Blocked` with status `dirty_overlap`.
