# Document stage — TIER-001 defect analysis

Stage: Document (2026-09-17). Inspected revision: `63e0083` (HEAD of `symphony/TIER-001`),
working tree clean at the time of inspection.

## Verdict

The Document lane found a real evidence defect, not a code defect. The engine change
looks correct, but the artefacts the ticket cites as proof contradict the ticket's own
`## QA Evidence`, `## AC Scorecard`, and `## Done Signals` claims. Per the Document lane
contract the ticket is routed back to `In Progress`; this lane does not repair evidence.

## Finding 1 — cited QA evidence is red, the ticket says green

| Ticket claim | Cited path | Actual committed content (HEAD) |
|---|---|---|
| `## QA Evidence` L352-357: `pyright` exit 0, "Clean static typing" | `docs/TIER-001/qa/pyright.txt` | `30 errors, 0 warnings, 0 informations` |
| `## QA Evidence` L358-363: pytest-cov exit 0, "Test suite passes with >= 80% coverage" | `docs/TIER-001/qa/pytest-cov.txt` | `9 failed, 2917 passed, 14 skipped, 210 warnings in 208.65s` |
| `## Done Signals` L275: "`pyright.txt`: `0 errors, 0 warnings, 0 informations`" | `docs/TIER-001/qa/pyright.txt` | same file says `30 errors` |
| `## Done Signals` L276: "`pytest-cov.txt`: `2926 passed, 14 skipped`, 84.12%" | `docs/TIER-001/qa/pytest-cov.txt` | same file says `9 failed, 2917 passed` |

Verified with `head -12 docs/TIER-001/qa/pyright.txt`, `tail -5 docs/TIER-001/qa/pyright.txt`,
`tail -6 docs/TIER-001/qa/pytest-cov.txt`. `git status --short` is empty, so the working
tree equals HEAD — this is not an uncommitted-local artefact.

The specific failing tests recorded in `pytest-cov.txt` are
`tests/test_webapi_auth.py::test_token_set_gates_get_requests`,
`tests/test_webapi_auth.py::test_token_set_gates_mutation_routes_too`,
`tests/test_webapi_auth.py::test_health_is_public_and_never_returns_service_probe_credential`,
`tests/test_webapi_auth.py::test_token_query_param_rejected_on_plain_routes`, plus the
`tests/test_doctor.py` / `tests/test_web_policy.py` failures already diagnosed in the
ticket's `## QA Failure` section as host-environment leakage.

## Finding 2 — root cause: later commits overwrote the genuine green run

`git log -p -- docs/TIER-001/qa/pyright.txt`:

```
2c8bbb4  +30 errors, 0 warnings, 0 informations      (Verify pass 1, RED — correct for that moment)
75fbe2b  -30 errors  +0 errors, 0 warnings, 0 informations   (GREEN, hermetic re-run)
1434c9a  -0 errors   +30 errors, 0 warnings, 0 informations  (REGRESSION — clobbered)
```

`git show 75fbe2b:docs/TIER-001/qa/pytest-cov.txt | tail -3`:

```
TOTAL                                            26737   4245    84%
Required test coverage of 80% reached. Total coverage: 84.12%
2926 passed, 14 skipped, 210 warnings in 210.70s (0:03:30)
```

That is exactly what `## Done Signals` L275-276 claims. So the green run happened and was
recorded; `1434c9a` ("docs(verify): add verification evidence for TIER-001") then replaced
`pyright.txt` with the direct-invocation output and truncated `pytest-cov.txt` to 6 partial
progress lines, and `63e0083` regenerated it as `9 failed, 2917 passed`.

The regenerating runner is `run_qa.sh` (added in `63e0083`), which hardcodes the two
commands already known to be environment-sensitive:

- `run_qa.sh:12` — `.venv/bin/pyright` (direct invocation; bypasses the `symphony-pyright`
  wrapper that supplies the interpreter and resolves third-party imports).
- `run_qa.sh:20` — pytest with no hermetic env, so host daemon variables
  (`SYMPHONY_API_*`, `SYMPHONY_TRUSTED_ORIGINS`, `SYMPHONY_WORKFLOW_DIR`) leak in.

Both are precisely the two causes the ticket itself documents under `## QA Failure` and
`## Self-Critique`. The fix was real; the evidence-recording step was not.

## Finding 3 — the code itself is unchanged since the green run

```
$ git diff --stat 75fbe2b..HEAD -- src tests docs/symphony-prompts README.md
(empty)
```

No source, test, prompt, or README change landed after `75fbe2b`. The green numbers from
`75fbe2b` therefore still describe the current code; only the recorded evidence is stale.
This is why the defect is routed as an evidence problem, not a behaviour problem.

## Finding 4 — out-of-scope files committed

`git show 63e0083 --stat` adds two files that are not in the ticket's `## Allowed files`:

- `append.md` (78 lines) — a scratch copy of ticket sections (`## Security Audit`,
  `## Review`, `## QA Evidence`, ...) used to append to the board card.
- `run_qa.sh` (30 lines) — the non-hermetic QA runner described in Finding 2.

The AC Scorecard row `Allowed files only | Git | PASS` cites
`docs/TIER-001/qa/git-diff-check.txt`, which contains only:

```
How to re-run: git diff --check develop..symphony/TIER-001
```

`git diff --check` reports whitespace errors only; it cannot prove file scope. The row is
therefore unsupported as written — a scope proof such as `git diff --name-only` is missing.

## Finding 5 — preflight hash no longer covers the tip

`docs/TIER-001/qa/merge-tree.txt` records `077230b3214a0ab3bfe803ddb3de7687cd0be619`,
captured in `8c7ea9b` ("record final preflight merge-tree hash"). `1434c9a` and `63e0083`
both changed tracked files after that commit, so the recorded merge-tree OID no longer
corresponds to the branch tip and does not discharge the `## Merge Status` preflight.

Direct re-run was not possible in this sandbox: `git merge-tree --write-tree` writes to the
object store and is gated here. This is an environment limit, not a work item.

## Finding 6 — stale AC Scorecard row emits the host contract warning

The ticket carries two `## AC Scorecard` tables. The first (pre-rewind, L227-236) still has
`Tests, Ruff, Pyright, Coverage pass | CI Checks | FAIL`. `src/symphony/orchestrator/contracts.py:46-49`
documents that `_scorecard_all_pass` surfaces any fail/error/empty result cell as a soft
`[contract-warn]`, which is the `## Contract Warning` at the ticket tail. The row is
superseded by the second table (L371-380) but is still read.

## Exact scope to fix (In Progress pass, no behaviour change intended)

1. Remove `append.md` and `run_qa.sh` from the branch; keep scratch runners outside the
   tracked tree (or gitignore them).
2. Re-run pyright via `.venv/bin/symphony-pyright` and pytest under the hermetic env
   (`env -u SYMPHONY_API_TOKEN_FILE -u SYMPHONY_API_AUTH_MODE -u SYMPHONY_TRUSTED_ORIGINS
   -u SYMPHONY_WORKFLOW_DIR ... pytest -q --cov=src/symphony --cov-report=term
   --cov-fail-under=80`); rewrite `docs/TIER-001/qa/pyright.txt` and
   `docs/TIER-001/qa/pytest-cov.txt` with the real output and a re-run line that names the
   command actually used.
3. Either correct the AC Scorecard row that claims `git diff --check` proves "allowed files
   modified", or add a real scope proof (`git diff --name-only develop..symphony/TIER-001`)
   to `docs/TIER-001/qa/`.
4. Re-run `git merge-tree --write-tree develop symphony/TIER-001` and refresh
   `docs/TIER-001/qa/merge-tree.txt` with the post-`63e0083` hash.
5. Mark the pre-rewind `## AC Scorecard` table superseded, or otherwise clear its `FAIL`
   cell, so the host stops emitting `## Contract Warning`.
6. Re-run Verify afterwards; Document then completes the llm-wiki write-back with evidence
   that matches the files on disk.

## Not done in this lane

- No source, test, prompt, README, or CHANGELOG edit (`README.md` precedence claim was
  checked and is accurate: README L277 is Tier 3 ticket `agent.profile`, L279 is Tier 5
  `agent.stage_profiles[state]`, matching the new L390 bullet).
- No commit, tag, branch, or push.
- `docs/llm-wiki/` write-back deferred: the wiki entry would have to assert verified
  tiered routing, and the verification evidence is exactly what is in question.

---

# Document pass 2 — completion audit (2026-09-17)

Inspected revision: `336499a` (tip of `symphony/TIER-001`), working tree clean.
The 6-step fix scope above was executed by the rewind; this pass audits the result and
completes the lane. **Verdict: no defect. The earlier findings are resolved.**

## What was re-checked, and what it showed

| Claim in the ticket | Independent check in this lane | Result |
|---|---|---|
| Mutation sync implemented | `git diff develop..symphony/TIER-001 -- src/symphony/workflow/mutate.py` | one `_rename_state_keyed_map(agent, "stage_profiles", ...)` added at each of the two sites (L254, L611) — matches the brief exactly |
| All 8 prompt contracts present | `grep -c` for each literal in `deep/plan.md` | all 8 found (`small-free` x2, `large-capable` x2, and 1 each for the remaining six) |
| Prompt stays succinct | `plan.md` has 26 lines / `text.count("\n") == 26` | passes the `<= 45` budget in `tests/test_workflow_presets.py:92` |
| Tests assert the contracts | `git diff ... -- tests/test_workflow_mutate.py tests/test_workflow_presets.py` | 3 new tests + 8 new assertions, exactly the set the brief names |
| README documents all 6 rules | read of the `#### Example 3` block | all 6 present |
| Precedence claim is accurate | `config.py:332-340` docstring + tiers at L370-392 | ticket `agent_profile` is tier 3, `agent.stage_profiles[state]` is tier 5 — README is correct |
| `opencode` has no profile `model:` | `PROFILE_FIELDS_BY_KIND` (`constants.py:141`) | `opencode` allows `command` but **not** `model` — README is correct, and the `--model`-in-command advice is load-bearing |
| Only allowed files changed | `git diff --name-only develop..symphony/TIER-001` | 15 paths, every one inside `## Allowed files` |
| Whitespace clean | `git diff --check develop..symphony/TIER-001` | empty output, exit 0 |

## Merge preflight — the recorded hash is snapshot-bound (not a defect)

`docs/TIER-001/qa/merge-tree.txt` holds `a3a9b2c9…`, recorded in `1353912` (tree
`e4a457f`). The tip is now `336499a` (tree `d1d8e15`), so the hash describes an earlier
tree. This is **not** rewind-worthy, for two independent reasons:

1. **The merge is provably conflict-free.** `git rev-parse develop` returns
   `4ad0cb73edab0443668267e3ea6b7e108d5edf2b`, which is *identical* to
   `git merge-base develop symphony/TIER-001`, and
   `git diff --name-only 4ad0cb7 develop` is empty. `develop` has not moved at all since
   the branch point, so `develop` is an ancestor of the tip: there is no second side to
   conflict with. Merging `--no-ff` produces the branch tree unchanged.
2. **The staleness is structural, not a mistake.** Any Document-stage write changes the
   tip, so a merge-tree hash can never cover the commit that Document itself produces.
   Re-recording it each pass would re-invalidate itself; the previous pass did exactly
   that and it regressed within two commits.

A hash that is merely old is not evidence *contradicting* a claim — the `## Merge Status`
section asserts the command, not a specific hash, and the property it exists to establish
is proven above. Rewinding again would be a loop with no fix at the end of it.

## Environment limit: live gates could not be re-executed in this lane

`pytest`, `ruff`, `symphony-pyright`, and `git merge-tree --write-tree` were all denied in
this worktree during the Document pass. Per [[agent-profile-resolution]] this is the known,
accepted condition of the ticket worktree, and the pipeline's own rule is that a sandbox
refusing a local command is an environment limit, not lost work — the host gate records
the same delivery moments later. Consequence for this pass: **the recorded run outputs
were audited for internal consistency and against their cited claims, but not regenerated.**
They are mutually consistent (34 / 1 passed match the ticket; `pytest-cov.txt` ends
`2926 passed, 14 skipped`, 84.12% >= 80%). Treat the green gates as *recorded by Verify*,
not as *independently re-run by Document*.

## Docs updated in this pass

- `CHANGELOG.md` — `### Added` entry for tiered Build routing, `### Fixed` entry for the
  `agent.stage_profiles` mutation sync. Justified by project convention: the Unreleased
  section already carries `agent.stage_kinds` routing, lane-preset, and
  `docs/symphony-prompts/**` prompt changes.
- `docs/llm-wiki/planner-tiered-build-routing.md` — new topic entry.
- `docs/llm-wiki/INDEX.md` — its table row.
- `README.md` — already correct; **not** edited in this pass (verified only).

## Not done in this lane

- No source, test, or shipped-prompt edit; no commit, tag, branch, or push.
- `README.ko.md` still lacks the tiered-routing subsection — outside `## Allowed files`.
