### DOCUMENT -- document the change and make the next ticket easier

Read `docs/{{ issue.identifier }}/{work,qa}/`, prior sections, and `docs/llm-wiki/`. Write wiki files and ticket comments only. Do NOT edit source, do NOT run git history commands, and do NOT run the Merge Gate here; Verify already did it.

1. Read `## Plan`, `## Implementation`, `## Self-Critique`, `## QA Evidence`, `## AC Scorecard`, and `## Merge Status`. Compare brief vs reality: assumptions that held or broke, constraints that surfaced, misleading wiki entries, evidence still `Not proven`.
2. Update `docs/llm-wiki/`: append a Decision-log row or create/update `docs/llm-wiki/<topic-slug>.md`, then add/refresh its row in `INDEX.md`.
3. Append `## Learnings` -- 3-4 bullets future work should not rediscover.
4. Append `## Wiki Updates` -- paths created/modified/removed, one line each.
5. Append `## As-Is -> To-Be Report` with subsections: `### Goal`, `### As-Is`, `### To-Be`, `### Reasoning`, `### Evidence` (commands/proofs with pass/fail, top evidence path), `### Not Covered`, `### How To Re-run`.
6. Only if a real critical/manual intervention remains, append `## Human Review` with: `### Intervention Required`, `### What Changed`, `### Why It Matters`, `### Evidence`, `### Risks`, `### Human Checklist` (3-5 quickly verifiable checkboxes), `### Decision Needed` (exactly one line: `Confirm Done` or `Do not confirm; move back to <state> because <reason>`).
7. Final History Gate -- Symphony runs it, not you:
   - Update the ticket frontmatter to `Done` for normal success, or `Human Review` only for the intervention branch. Do not stage, commit, or publish the delivery record yourself.
   - After this stage exits, the orchestrator commits the workspace, publishes the branch, re-reads the remote tip with `git ls-remote`, and writes the SHAs into the ticket. Record what you produced and where; leave SHAs to the host gate.
   - Never set `Blocked` because a local git command failed -- a sandbox that refuses `.git/objects` writes is an environment limit, not lost work; the host gate records the same delivery moments later.
8. Set state to `Done` for normal success. Set state to `Human Review` only for the recorded critical/manual intervention branch.

Operator skip: the TUI/web skip action may append `## Document Skipped` and move an idle Document ticket to `Human Review`; agents must not simulate that skip themselves.
