# SMOKE-003 Runtime Refusals (Verify)

Run: 2026-08-15T12:07:26Z. Commands the harness permission policy refused this turn; each attempted once.

| Command | Refusal | Workaround used |
| --- | --- | --- |
| `git merge-tree --write-tree main symphony/SMOKE-003` | "This command requires approval" | Fallback topology proof in `qa/merge-preflight.md` (merge-base == main tip, added-only paths) |
| `printf 'OK\n' > /tmp/expected` | "Output redirection to '/tmp/expected' was blocked" (only session working dirs writable) | Reference fixture written inside the workspace: `qa/expected-ok.txt` |

Everything else this turn (git read-only verbs, `ls`, `cat`, `cmp`, `wc`, `od`, `sha256sum`, `grep`, `printf > workspace-path`, `mkdir`, `cd`) ran normally.

Impact on evidence: none — the fallback proof is equivalent for this ticket because the branch is exactly one commit ahead of main and adds only two new paths.
