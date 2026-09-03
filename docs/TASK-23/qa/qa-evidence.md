# QA Evidence — TASK-23

**What**: Proved `verify-upstream-smoke.txt` exists at the repo root and its content is byte-exact.
**Why**: The single acceptance criterion is a literal-content check; passing it is the whole deliverable.
**As-Is -> To-Be**: No prior evidence file -> full command manifest + merge preflight recorded below.

## Command manifest

| Command | Exit code | Evidence path | Proves | Does not prove | How to re-run |
| --- | --- | --- | --- | --- | --- |
| `test -f verify-upstream-smoke.txt` | 0 | `qa/acceptance-checks-raw.txt` | File exists at repo root | Content correctness | `cd $(git rev-parse --show-toplevel) && test -f verify-upstream-smoke.txt` |
| `wc -c` / `wc -l` on file | 0 | `qa/acceptance-checks-raw.txt` | File is 15 bytes, 1 line | Exact byte sequence | `wc -c verify-upstream-smoke.txt; wc -l verify-upstream-smoke.txt` |
| `diff` against `printf 'all systems go\n'` | 0 (no diff) | `qa/acceptance-checks-raw.txt` | Content is byte-exact `all systems go` + trailing newline | Anything about other repo files | `diff <(printf 'all systems go\n') verify-upstream-smoke.txt` |
| `xxd verify-upstream-smoke.txt` | 0 | `qa/acceptance-checks-raw.txt` | Hex dump confirms no stray CR/BOM/extra whitespace | N/A | `xxd verify-upstream-smoke.txt` |
| `git --git-dir=<host>/.git merge-tree --write-tree develop symphony/TASK-23` | 0 | `qa/merge-tree.log` | Branch merges cleanly into `develop` with no conflicts | Runtime behavior after merge | `git --git-dir=/home/symphony/git/oh-my-symphony/.git merge-tree --write-tree develop symphony/TASK-23` |
| `git diff --name-only develop..symphony/TASK-23` | 0 | this file | Change scope limited to `verify-upstream-smoke.txt` + `docs/TASK-23/work/*` | N/A | `git --git-dir=/home/symphony/git/oh-my-symphony/.git diff --name-only develop..symphony/TASK-23` |

No unit/integration test suite references this file (static content deliverable, no runtime code path) — confirmed via `grep -rn "verify-upstream-smoke" --include="*.py" .` returning no matches, consistent with the ticket's stated chore rationale.
