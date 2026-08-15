# SMOKE-003 Merge Preflight (Verify)

Re-run 2026-08-15T12:11Z after Verify rewind (missing exact `## Merge Status` heading). Non-ignored mirror of `qa/merge-tree.log` (`.log` files are gitignored, so this `.md` is what rides the Done merge).

## Target resolution

- `agent.auto_merge_target_branch` = `dev` — missing ref: `git rev-parse --verify dev` -> `fatal: Needed a single revision`
- `agent.feature_base_branch` = `dev` — same missing ref
- Fallback: host current branch = `main` (host `.git/HEAD` -> `ref: refs/heads/main`)

## Preflight command

- Command: `git merge-tree --write-tree main symphony/SMOKE-003`
- Result: DENIED by harness permission policy ("This command requires approval"). No merge was created.

## Fallback topology proof (allowed read-only git verbs, all exit 0)

- `git rev-parse main` -> `501a4c0f2f145b03350042fbe7f6eab61fbe9fda`
- `git merge-base main HEAD` -> `501a4c0f2f145b03350042fbe7f6eab61fbe9fda` — main tip IS the merge base; main is a direct ancestor of HEAD (`9e7e825`)
- `git diff --name-only main..HEAD` -> 8 paths, all added (`A`) vs main, none existing on main -> zero content-conflict surface:
  - `docs/SMOKE-003/qa/ac-checks.md`, `docs/SMOKE-003/qa/expected-ok.txt`,
    `docs/SMOKE-003/qa/merge-preflight.md`, `docs/SMOKE-003/qa/runtime-blocked.md`,
    `docs/SMOKE-003/qa/security-audit.md`, `docs/SMOKE-003/verify/details.md`,
    `docs/SMOKE-003/work/implementation-notes.md`, `smoke.txt`
- Working tree clean (`git status` -> nothing to commit) — no dirty tracked files to overlap

## Conclusion

Preflight CLEAN (fast-forward topology, no possible conflicts, no dirty-file overlap). Orchestrator will create the single `--no-ff` merge at Done. This agent created no merge commit.

## How to re-run

```bash
git rev-parse main
git merge-base main HEAD
git diff --name-only main..HEAD
git status
```

Expected: main SHA == merge-base SHA (ancestor topology); only the 8 ticket paths listed; clean tree.
