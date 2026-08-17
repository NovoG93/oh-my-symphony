# TASK-9 QA — Acceptance Checks (Verify, 2026-08-17)

Goal: prove `profile-smoke.txt` exists at repo root with exactly the single line `OK`.

## Transcript (run in worktree root)

| # | Command | Exit | Output |
|---|---------|------|--------|
| 1 | `ls -la profile-smoke.txt` | 0 | `-rw-rw-r-- 1 symphony symphony 3 Aug 17 16:44 profile-smoke.txt` |
| 2 | `cmp profile-smoke.txt docs/TASK-9/work/expected-ok.txt` | 0 | (silent — byte-identical) |
| 3 | `wc -l -c profile-smoke.txt` | 0 | `1 3 profile-smoke.txt` |
| 4 | `od -A x -t x1z profile-smoke.txt` | 0 | `000000 4f 4b 0a >OK.<` / `000003` |
| 5 | `sha256sum profile-smoke.txt` | 0 | `a12b7cb43c9d9134b5bb1b35e9096b66775d9e92e7611d1cc92b02edd6782a87` |
| 6 | `git diff --name-only develop symphony/TASK-9` | 0 | 3 paths: `docs/TASK-9/work/expected-ok.txt`, `docs/TASK-9/work/implementation-notes.md`, `profile-smoke.txt` |
| 7 | `git status` (start of turn) | 0 | `nothing to commit, working tree clean` |

## What this proves / does not prove

- Checks 1-5 prove the working-tree file at repo root is 3 bytes `4f 4b 0a` = exactly one line `OK`, byte-identical to the committed fixture and to the hash recorded in `work/implementation-notes.md` (AC1, AC2). No extra lines, no leading/trailing whitespace.
- Check 6 proves the branch adds only the deliverable and its work artefacts — no orphan scope.
- Does not prove: post-merge state on `develop` (orchestrator merges at Done); nothing here runs code, and none is required for a static text deliverable.

## How to re-run

```bash
ls -la profile-smoke.txt
cmp profile-smoke.txt docs/TASK-9/work/expected-ok.txt
wc -l -c profile-smoke.txt
od -A x -t x1z profile-smoke.txt
sha256sum profile-smoke.txt
git diff --name-only develop symphony/TASK-9
```

Expected: exit 0 for all; sha256 `a12b7cb43c9d9134b5bb1b35e9096b66775d9e92e7611d1cc92b02edd6782a87`.
