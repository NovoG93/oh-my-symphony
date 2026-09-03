# Repro Evidence

## Repro Command
```bash
git -C /home/symphony/git/oh-my-symphony diff --name-only
```

## Before State (Reproduction)
Host repo `/home/symphony/git/oh-my-symphony` had:
- `WORKFLOW.md` (modified)
- `docs/llm-wiki/INDEX.md` (modified with stray `(stale?)` markers)

Branch `symphony/TASK-10` had modifications on `docs/llm-wiki/INDEX.md`.
Running merge safety block detected `docs/llm-wiki/INDEX.md` as overlapping dirty file, returning exit code 41 (`dirty_overlap`).
