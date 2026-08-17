# Preflight Verification Evidence

## Merge Preflight Test (TASK-11)
Command:
```bash
git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree develop symphony/TASK-11
```
Result: Exited 0 with tree `798121874e8c7ef257fe09d620c89368021921ef`.

## Dirty Overlap Check (TASK-11 vs Host)
Command:
```bash
git -C /home/symphony/git/oh-my-symphony --literal-pathspecs diff --quiet develop..symphony/TASK-11 -- WORKFLOW.md
```
Result: Exited 0 (no overlap between dirty host files and branch diff).

## Merge Preflight Test (FIX-TASK-11-1)
Command:
```bash
git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree develop symphony/FIX-TASK-11-1
```
Result: Exited 0 with tree `55c9c6779249b090b1f824a7d3b4b4e583f65698`.

## Dirty Overlap Check (FIX-TASK-11-1 vs Host)
Command:
```bash
git -C /home/symphony/git/oh-my-symphony --literal-pathspecs diff --quiet develop..symphony/FIX-TASK-11-1 -- WORKFLOW.md
```
Result: Exited 0 (no overlap between dirty host files and branch diff).

## Full Test Suite
Command:
```bash
/home/symphony/symphony_workspaces/FIX-TASK-11-1/.venv/bin/pytest /home/symphony/symphony_workspaces/FIX-TASK-11-1/tests -q
```
Result: 2421 passed, 9 skipped, 1 warning in 177.19s (exit code 0).

## TASK-11 Removal & History Checks
Commands:
```bash
test ! -e /home/symphony/symphony_workspaces/TASK-11/profile-smoke.txt
git -C /home/symphony/symphony_workspaces/TASK-11 ls-files profile-smoke.txt
git -C /home/symphony/symphony_workspaces/TASK-11 merge-base --is-ancestor 62a5734 HEAD
```
Result:
- AC1: `profile-smoke.txt` absent (exit 0).
- AC2: `git ls-files` returned empty (exit 0).
- AC3: `62a5734` is an ancestor of HEAD (exit 0).

## How to Re-run
```bash
git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree develop symphony/TASK-11
git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree develop symphony/FIX-TASK-11-1
/home/symphony/symphony_workspaces/FIX-TASK-11-1/.venv/bin/pytest /home/symphony/symphony_workspaces/FIX-TASK-11-1/tests -q
```
