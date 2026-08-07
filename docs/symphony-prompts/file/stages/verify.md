### VERIFY -- prove it and merge safely

Read the full diff, tests, `docs/{{ issue.identifier }}/work/`, and ticket sections. Write evidence under `docs/{{ issue.identifier }}/qa/` and ticket comments; run real commands. Do NOT make unrelated source edits.

Verify has three jobs: review, QA, and merge preflight/merge. The card must say what worked, what failed, what is not covered, how to re-run the proof, and whether the branch merged cleanly.

1. Review the diff against the ticket, `## Plan`, `## Acceptance Tests`, and `## Done Signals`. No orphan scope.
2. Append `## Security Audit` with exactly 7 rows: secrets, input-validation, injection, xss, csrf, authz, rate-limit. Result `pass` / `fail` / `n/a`; every `pass`/`fail` row cites a durable `qa/...` or `work/...` artifact; never a source anchor like `todo.py:54`; `n/a` rows may carry a short reason instead.
3. Any CRITICAL/HIGH/MEDIUM issue -> append `## Review Findings` as a severity table (problem, evidence path, requested fix, scope), set state to `In Progress`, stop. Otherwise append `## Review` with the clean-review reason.
4. Run the real acceptance checks; save durable proof under `docs/{{ issue.identifier }}/qa/`, including a How to re-run line.
   - Trivial non-runtime changes may shorten QA: run the relevant static/content check and say why no runtime path changed.
   - Browser UI work must drive Playwright/headless Chromium against the exact declared launch path for core flows. If the app claims direct `file://` support, fail on module-script/CORS boot errors instead of switching to HTTP. DOM shims are smoke only, never final Verify authority. Missing browser deps -> append `## Environment Block`, set state to `Blocked`, stop.
   - Bugs: close the reproduction loop with `docs/{{ issue.identifier }}/qa/repro-after.log`.
   - Full integration gate for app-delivery/release tickets: run against the committed target branch, never an unmerged worker branch. Clean install/build, start, readiness probe, core workflows, console/network review. Failures -> append `## Integration Defects`, register new Kanban/board bug tickets (repro, logs, expected behavior, fix boundary, verification commands), add their IDs to `blocked_by`, set state to `Blocked`, stop; when blockers complete, rerun from scratch.
5. Append `## QA Evidence` -- command manifest: command, exit code, evidence path, what it proves, what it does not prove, how to re-run.
6. Append `## AC Scorecard` -- one row per acceptance criterion: signal, source, result, evidence path. Evidence cells must cite files under `docs/{{ issue.identifier }}/` as `qa/...` or `work/...` (backtick spans; source anchors and prose live inside the cited artifact, not the cell).
7. Any required command fails or evidence disproves an AC -> append `## QA Failure`, set state to `In Progress`, stop.
{% if agent.auto_merge_on_done %}
8. Merge Gate:
   - Resolve target in order: `agent.auto_merge_target_branch`, `agent.feature_base_branch`, current host branch.
   - From the host repo run `git merge-tree --write-tree <target-branch> symphony/{{ issue.identifier }}`; save output to `docs/{{ issue.identifier }}/qa/merge-tree.log`. Do not merge the target branch into the ticket workspace. Do not use `git status -uno --porcelain` as merge proof.
   - Committed conflicts -> set state to `Blocked`, append `## Merge Failure` (command, target branch, conflicted paths), stop.
   - Clean -> check host dirty tracked files against `git diff --name-only <target-branch>..symphony/{{ issue.identifier }}`; block only on real overlap.
   - Safe -> create the explicit `--no-ff` merge commit on the target branch; record target branch, feature branch, command, and merge SHA under `## Merge Status`.
{% else %}
8. Merge Gate is disabled (`agent.auto_merge_on_done` is false). Append `## Merge Status` noting this workflow intentionally leaves branch integration to the operator.
{% endif %}
9. Set state to `Learn`.
