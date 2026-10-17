# Factory quality ledger

Scope and approved bar: [.quality.md](.quality.md). Findings and causal evidence:
[PR 57 repair](docs/quality/pr57/REPAIR.md).

## Current state

- Result: **67%, FAIL**, average 3.375/5, independent receipt-based R9 assessment.
  The required quality bar is not met. The prior R8 baseline was 62%.
- Source review: reported runtime blockers are repaired; checkpoint `dcf9db12`
  passed independent review and full Linux checks (3520 tests plus 10 timing tests).
- Required target: every dimension >=4/5, average >=4.625/5, quality >90%,
  no failed floors. Current observed core journeys pass; F-2, F-6 and F-7 remain
  evidence gaps, not established breaches. No current blocker or data-loss defect
  is established by the final receipts.
- Delivery: verified repair to PR 57 only, with the unmet quality gate explicit.
  No merge/release.

| Dimension | Score / 5 | Why it is below 5 |
|---|---:|---|
| CLI experience | 3 | Native accessibility/settings and complete reference comparison are unverified; historical Blocker copy remains after recovery |
| Factory function | 3 | Complete kill/relaunch and atomic-write/update qualification is missing |
| Consumer trust | 4.5 | First-value comparison measures scripted card preparation rather than unassisted full delivery |
| Engineering | 3 | External request/data tracing and dependency objectives/slow-timeout evidence are incomplete |

The rubric caps a dimension at 3 when more than a quarter of applicable criteria
have evidence gaps. Quality is floor(3.375 / 5 x 100) = 67%. These caps do not
represent newly observed severe defects. All 27 criteria and ten floors were
assessed; applicable gaps independently block PASS.

The judge accidentally opened a generated observation serializer while selecting
receipts. It contained the same permitted observations and was excluded from
evidence. No product implementation, test, provider/driver source or builder score
was read; a perfectly clean read boundary is not claimed.

Remaining qualification: native NVDA and narrow/wide terminal/font operation;
full persistence interruption/update matrix; unassisted factory/advanced delivery
comparison; redacted external request inventory; defined dependency objectives
and a slow/timeout recovery run. Minor follow-ups are clearly resolved historical
Blocker text and compact human diagnostics with preserved copyable paths.

## Audit and repair rounds

### Audit 1: original PR 5358ac5

Fresh Windows factory result: 99 passed, 9 failed. Repository Ruff: 9 findings.
Pyright: 5 findings. Eleven files conflicted with the then-current dev target.
Score not assigned: product and artifact evidence incomplete.

### Fix 1: integration and contract repair

Preserved dev's modular orchestrator, required fresh Verify, kept honest Blocked
handoffs, matched pinned proof commands, repaired backend initialization and
preflight writes, restored package assets and native platform behavior.
Focused evidence: runtime 390 passed / 1 skipped; compatibility 292 passed /
19 skipped; factory tests later reached 157 passed. These counts preceded the
second dev integration and do not substitute for final checks.

### Fix 2: installed artifact boundaries

The first installed wheel exposed Windows junction data loss during cleanup and
automatic Blocked recovery producing incompatible repair cards. Cleanup now
detaches bounded link entries before removal; missing authority remains Blocked.
Real cleanup regressions preserve host card bytes and unrelated junction targets.
The next installed wheel completed both dependency tickets and local merges.

### Fix 3: independent sync review

Independent review reproduced duplicate concurrent imports and rollback losing
a newer worker state, then found a retained child could lose its dependency.
Board-level sync serialization, guarded rollback, and dependency-closure
preservation fix all three. Independent re-review: 25 tests pass, exact failure
and retry reproduced successfully, no source-review blockers.

### Fix 4: actual Codex startup and CLI usability

The real backend rejected Windows paths in its injected TOML grants. Equivalent
forward-slash command arguments and junction discovery restored exact host-board
and Git-directory access without widening sandbox mode. Actual initialization
and model discovery passed; backend tests: 155 passed / 6 existing skips, plus
21 POSIX checks. Fresh CLI replay verifies the repaired overwrite warnings,
argument descriptions, terminal usage widths and Windows port diagnostics.

Checkpoint `d990503` Linux: 3453 passed / 101 configured skips, 10 timing tests,
85.55% coverage; all static gates passed. Its installed wheel passed four fixture
journeys and five persona replays. These are historical after the next code fix.

### Fix 5: retain work when Git cannot commit

The real Codex Build and independent Verify executed the declared product tests.
With the 350000 Verify ceiling it paused at 355764 phase tokens; an operator-only
600000 ceiling resumed Verify, which completed. The host then failed to index
`product_task_1.py` with permission denied. It incorrectly continued through
`auto_merge_nothing_to_apply` and cleanup, deleting the undelivered product files.
This occurred only in the disposable QA project. No owner project was used.

The failed private temporary-directory ACL differs from a normal user project;
this is evidence for the permission-boundary diagnosis, not proof of the exact
sandbox ownership mechanism. It does not excuse cleanup after failed commit.
Work retention and an explicit recovery state are repaired in `005e66f`.
Independent review also found and closed dirty startup cleanup and release-handoff
snapshot bypasses. R6 installed-wheel QA passes the real index-lock failure and
explicit-resume recovery: no product loss or host merge before recovery, followed
by fresh Verify, delivery and safe cleanup. Test-only `f420cf1` corrects two old
reconcile mocks to return the actual durable-success result type; assertions and
enabled commit checks are unchanged.

### Fix 6: browser-proof applicability

The R6 live run used normal inherited Windows permissions. Build and Verify
executed the declared product tests and host Git successfully recorded the two
product files in commit `e751495`. However, the Python-only verifier's sentence
"No browser surface or attached `superqa` applies" triggered an incorrect demand
for Browser Verification. The product remained safely committed in its worktree;
delivery did not complete. Explicit skill/section applicability and the running
attempt's pinned requirement now replace the body-wide keyword match in
`01e449c`. R7 completed the real Codex journey; R8 repeats the negative-sentence
case and both positive browser requirements against the final wheel.

### Fix 7: truthful operator diagnostics

R7's real Codex journey completed Build, independent Verify, scoped commit
`3d80cf9`, local fixture merge `d32a7d9`, cleanup and a host-side product-test rerun.
No PR merge occurred. Fresh simulated operator sessions then exposed two inherited
diagnostic errors affecting factory use: a progress link still used port 9999
after an explicit port override, and the API merge policy described a Document
stage absent from the factory. `3d44b3b` repairs both diagnostics. Independent
review and 28 focused tests pass. An installed service configured for 63681 and
started on 63697 writes the 63697 progress link and reports `after Verify`.

The first three fresh-persona sessions also encountered a separate fixture error:
Windows resolved the supplied `codex.cmd`, while Git Bash resolved a different
extensionless executable. They were stopped and their incomplete results retained.
This is not counted as successful usability proof. The corrected fixture supplies
both wrapper forms and verifies the selected transport through actual backend
initialization before new fresh sessions. No product resolver or model-default
change was made to accommodate that fixture error.

### R8 independent product assessment

Exact wheel `30d0ef3f3e4606a173181364db8c8b44207923b8947bf60b786d67a7ddf8891c`
at runtime `3d44b3b5`: Experience 3, Function 3, Consumer 3.5, Engineering 3;
average 3.125/5, quality 62%, FAIL. The judge assessed all 27 criteria and ten
floors from build-bound receipts without reading implementation or tests.

All five fresh simulated operators completed their assigned delivery or recovery
journey. The real Codex attempt exposed a blocker: Verify exceeded its finite
phase token cap after writing Done, leaving no host delivery while retaining its
product worktree. Separate operator recovery succeeded; it does not erase the
original failure. The judge recorded F-1/F-3 failures, not an observed data-loss
breach. Accessibility/reference comparisons, atomic-write/update coverage,
external-host/data tracing and dependency objectives also remain evidence gaps.

### Fix 8: truthful token-cancellation recovery

The final qualification found a necessary safe-delivery defect within the
owner-approved scope. A focused regression reproduced the undelivered Done
state. A narrow factory cancellation handler now retains work, persists an
operator pause, and writes a safe state and actionable Blocker. Product token
defaults and intentional terminal cancellation behavior remain unchanged.
Independent source review passes, including the Completed-alias edge found by
the reviewer. The final R9 wheel passes all eight installed journeys. Its new
case verifies 664618/600000 cancellation, safe Blocked state, retained hashes and
registration, restart without dispatch, explicit resume and fresh Verify before
actual delivery. Final independent assessment closes the demonstrated R8 F-1/F-3
blocker on this artifact. The final configured real Codex attempt and all five
fresh simulated operator journeys also pass; remaining gaps keep the score at 67%.

## Regression guards

| Area | Guard |
|---|---|
| Delivery | Factory contracts and orchestrator transition/reconciliation tests |
| Initialization | Every supported backend; invalid inputs preserve existing bytes |
| Cleanup | Real Windows junction and POSIX symlink removal with target preservation |
| Package | Runtime closure, notices, execute-mode restoration, wheel inventory |
| Compatibility | Advanced project creation and current default/deep/release suites |
| Sync | Concurrent import, worker update, retained-child dependency closure, retry |
| Failed commit | Exit, reconciliation, startup and release-handoff retention tests; installed index-lock recovery |
| Browser applicability | Negative prose, explicit heading/skill and within-attempt removal tests |
| Operator diagnostics | Port override, assigned port, reload and explicit-URL tests; profile policy text tests |
| Token cancellation | Successful-state aliases, persisted pause across tracker failures/restart, explicit resume; installed overrun-to-recovery journey |

Final receipts bind source and wheel SHA-256 values. Source changes invalidate
the corresponding runtime receipts; upstream PM limitations stay explicit.
