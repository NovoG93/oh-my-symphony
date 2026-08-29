# Runtime-blocked checks — TASK-23 (Document rewind turn)

| Command | Refusal | What it would have proven | Substitute evidence |
| --- | --- | --- | --- |
| `git merge-tree --write-tree develop HEAD` | "This command requires approval" (sandbox, 2026-08-29) | Clean merge of tip `e80528b` into `develop` at rewind time | Recorded preflight at `qa/merge-tree.log` (exit 0, tree `528d1edd…`, tip `7b2371f`); only `docs/TASK-23/qa/*` files added since (no source delta); fast-forward topology: `git merge-base develop HEAD` == `develop` (`d97795af`) -> conflicts impossible |

Re-run when permitted: `git merge-tree --write-tree develop HEAD`
