### LEARN -- make the next ticket easier

**Allowed tools (advisory).** Read `docs/{{ issue.identifier }}/{work,qa}/`, prior ticket sections, and `docs/llm-wiki/`. Write wiki files and ticket comments only. Do NOT edit source, do NOT run git history commands, and do NOT run the Merge Gate here; Verify already did it and Symphony records the history itself.

Goal for this lane: turn one ticket's evidence into reusable memory and close normal successful work as Done. Use Human Review only when a real critical/manual intervention remains and the agent cannot resolve it locally.

1. Read `## Plan`, `## Implementation`, `## Self-Critique`, `## QA Evidence`, `## AC Scorecard`, and `## Merge Status`.
2. Compare brief vs reality: the user's goal, before state, after target, assumptions that held or broke, constraints that surfaced, prior wiki entries that were incomplete or misleading, and evidence that remains `Not proven`.
3. Update `docs/llm-wiki/`: append a Decision-log row to an existing entry, or create/update `docs/llm-wiki/<topic-slug>.md`, then add/refresh its row in `INDEX.md`.
4. Append `## Learnings` -- 3-4 bullets of new facts, constraints, surprises, or rejected approaches that future work should not rediscover.
5. Append `## Wiki Updates` -- paths created/modified/removed, one line each.
6. Append `## As-Is -> To-Be Report` with this shape:
   - `### Goal` -- user outcome in plain language.
   - `### As-Is` -- prior behavior, with evidence.
   - `### To-Be` -- new behavior, with matching evidence.
   - `### Reasoning` -- approach, alternatives rejected, and trade-offs.
   - `### Evidence` -- commands/proofs with pass/fail, top evidence path, and how to re-run.
   - `### Not Covered` -- residual risks, follow-ups, or `none`.
   - `### How To Re-run` -- exact command or evidence path.
7. Only if a real critical/manual intervention remains, append `## Human Review` with this shape:
   - `### Intervention Required` -- the manual decision, credential, external system, or approval needed.
   - `### What Changed` -- 2-3 bullets.
   - `### Why It Matters` -- 1-2 bullets.
   - `### Evidence` -- commands/proofs with pass/fail, top evidence path, and how to re-run.
   - `### Risks` -- residual risks, not-covered areas, follow-ups, or `none`.
   - `### Human Checklist` -- 3-5 quickly verifiable checkboxes.
   - `### Decision Needed` -- exactly one line: `Confirm Done` or `Do not confirm; move back to <state> because <reason>`.
8. Final History Gate -- Symphony runs it, not you:
   - Update the ticket to `Done` for normal success, or `Human Review` only for the intervention branch. Do not stage, commit, or publish the delivery record yourself.
   - After this stage exits, Symphony's orchestrator commits the workspace, publishes the branch when it has an upstream, and re-reads the remote tip with `git ls-remote`. It runs outside your sandbox, so it can write the object database even where you cannot, and it writes the resulting SHAs into the ticket.
   - In `### Evidence`, record what you produced and where. Leave branch and commit SHAs to the host gate.
   - Never set `Blocked` because a local git command failed. A sandbox that refuses `.git/objects` writes is an environment limit, not lost work -- the host gate records the same delivery moments later. If the remote genuinely cannot be verified, Symphony moves the card to `Human Review` itself and says why.
9. Set state to `Done` for normal success. Set state to `Human Review` only for the recorded critical/manual intervention branch.

Operator skip: the TUI/web skip action may append `## Learn Skipped` and move an idle Learn ticket to `Human Review` for explicit operator review. Agents must not simulate that skip themselves.
