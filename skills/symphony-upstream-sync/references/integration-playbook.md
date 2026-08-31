# Upstream integration playbook

Use this procedure for a real upstream synchronization in this fork. Substitute discovered refs and hashes; values in the existing ledger describe the previous integration, not the next one.

## 1. Establish the boundary

Preserve the current worktree before doing anything. Record staged, unstaged, and untracked paths and do not absorb unrelated user files into the integration.

Fetch both remotes, then inspect rather than assume:

```bash
git fetch upstream --prune
git fetch origin --prune
git status --short --branch
git remote -v
git rev-parse develop origin/develop upstream/dev upstream/main
git log --reverse develop..upstream/dev
git log --left-right --cherry-pick --oneline develop...upstream/dev
git merge-base develop upstream/dev
git log --oneline upstream/dev..upstream/main
```

Read `docs/upstream-sync.md` and verify its previous upstream head is already an ancestor of `develop`. If moved refs, unexpected ancestry, or rewritten upstream history make that false, stop for review.

Select the upstream ref from the requested scope. `upstream/dev` is the default functional line; include `upstream/main`-only release/docs work only when requested and reviewed.

Create a fresh feature branch from current local `develop`. Use a date or target hash in its name when useful. Do not reuse a branch whose history does not match the ledger.

```bash
git switch develop
git switch -c feat/merge-upstream-<date-or-head>
```

Set local merge assistance:

```bash
git config --local merge.conflictStyle zdiff3
git config --local rerere.enabled true
git config --local rerere.autoupdate false
```

Run a baseline proportional to the incoming range. Run `symphony doctor ./WORKFLOW.md`; in this canonical checkout, classify the protected-repository refusal separately rather than weakening it.

## 2. Open and resolve M1

```bash
git switch <feature-branch>
git merge --no-ff --no-commit upstream/dev
```

Keep the merge open until conflict review and the full pre-M1 gate pass. Audit every commit in the newly offered range:

```bash
git log --reverse <previous-upstream-head>..upstream/dev
git show --stat <commit>
git show <commit> -- <relevant-paths>
```

Assign each commit one ledger disposition:

- `applied`: behavior arrived without material adaptation.
- `adapted`: intent is present through fork-aware integration.
- `partially deferred`: identify the exact included and excluded surfaces and why.

Do not label a commit applied merely because Git staged its patch.

### Fork invariants to review

These are regression surfaces, not a license to keep stale code:

- named agent profiles, per-stage routing, session identity, and dynamic backend selection;
- Copilot backend behavior and monthly/provider usage reporting;
- usage pools, caps/windows, quota events, authoritative versus estimated readings, and capacity-paused rendering;
- inbound MCP authentication versus independent outbound API credentials, including artifact downloads;
- release verification, release authority, RCA creation/reconciliation, and their registry records;
- workspace, project registry, service lifecycle, and process identity checks;
- web origin/content-type checks, API routes/payloads, chat WebSocket token scope, and tab-scoped API token storage;
- workflow validation, file-board behavior, i18n, and fork-specific UI states.

The current ledger deliberately defers the `_AgentPhaseState`, `_AgentPhaseTransition`, and `_transition_agent_phase` rewrite from upstream commit `7981dc1`. Continue using the fork's active inline phase-transition flow unless the user explicitly requests that refactor. When later upstream work depends on it, adapt the non-phase behavior and document the remaining boundary.

### Conflict discipline

For every conflict:

1. Read base, local, and upstream sections provided by `zdiff3`.
2. Inspect the relevant upstream commit, not just the final upstream file.
3. Identify fork-only callers, tests, configuration, and data fields.
4. Write a behavioral union or an intentional adaptation.
5. Add or update tests for the integration seam.
6. Remove markers, run `git diff --check`, and stage only the resolved result.

If several workers help, run them sequentially in the same open merge. Suggested ownership batches are process/platform, auth/service/MCP, backend/event-loop, and TUI/web. The actual incoming range determines the batches; do not port changes that are not present.

## 3. Pre-M1 acceptance

Use commands defined by the current project metadata rather than stale copied flags. At minimum require:

```bash
uv sync --locked
uv lock --check
uv run ruff check src tests
uv run pyright
uv run pytest
uv run symphony doctor ./WORKFLOW.md
git diff --cached --check
git diff --name-only --diff-filter=U
```

Also run the repository's i18n validation, MCP import/CLI smoke, Node syntax checks, and focused tests for every changed security or lifecycle boundary. Verify the lockfile contains the current project version and declared MCP dependencies.

For Windows-sensitive changes, require a real Windows runner for process-tree termination, PID reuse, junction cleanup, NTFS locking, path/argv handling, long command scripts, hook capture, and managed service stop. If unavailable, stop before M2 unless the user explicitly waives the gate. Record what remains unproven.

Create M1 only after review:

```bash
git commit
```

Use a merge subject that identifies the integrated upstream head. The body must list conflict files and strategies, intentional deferrals, and trailers containing the full merge base, upstream head, and observed upstream/main head.

## 4. Ledger follow-up

Create or update `docs/upstream-sync.md` in a small commit after M1. Include:

- remote URLs, selected refs, merge base, integrated head, and observed upstream/main;
- every newly offered upstream commit and its disposition;
- M1 hash and parents;
- conflict rationale and deliberately deferred surfaces;
- exact focused/full test evidence and environment-sensitive classifications;
- platform evidence or explicit waiver;
- excluded upstream-only ranges and the reason;
- future discovery/audit commands;
- M2 marked pending.

Do not invent M2's hash.

## 5. Synchronize and promote

Fetch `origin` again immediately before promotion. Compare local and remote `develop`:

- If local `develop` is strictly behind, fast-forward with `git merge --ff-only origin/develop`.
- If they diverged, stop. Do not rebase, reset, or force-update.
- If `develop` advanced since the feature branch was created, merge current `develop` into the feature branch with `--no-ff`, resolve there, and rerun the complete acceptance suite.

Obtain explicit approval for the local `develop` promotion when required by the execution environment. Then create M2:

```bash
git switch develop
git merge --no-ff <feature-branch> -m "merge: integrate upstream <scope>"
```

Do not allow a fast-forward even when one is possible. Do not push.

## 6. Verify M2 and finalize the ledger

From `develop`, rerun full pytest and all static checks. Do not rely only on feature-branch results.

Verify ancestry using exact hashes:

```bash
git merge-base --is-ancestor <upstream-head> develop
git merge-base --is-ancestor <M1> develop
git merge-base --is-ancestor <feature-branch> develop
git log develop..upstream/dev
git log --graph --decorate --oneline --all
git log --first-parent --merges develop
```

Acceptance requires all ancestor checks to succeed, `develop..upstream/dev` to be empty for the integrated head, and M1 to appear beneath M2. Confirm the feature was not squashed, rebased, or reconstructed.

Finally, update the ledger with M2's full hash, parents, subject, post-M2 evidence, and any waiver. Commit that docs-only change on `develop`, explaining that post-M2 finalization is necessary because M2 cannot contain its own hash.

## 7. Subsequent syncs and rollback

Discover the next range from ancestry rather than patch equivalence:

```bash
git fetch upstream --prune
git log --reverse develop..upstream/dev
git log --left-right --cherry-pick --oneline develop...upstream/dev
git merge-base develop upstream/dev
git range-diff <previous-upstream-head>..upstream/dev <prior-local-tip>..HEAD
git log --first-parent --merges develop
```

Manual adaptations remain authoritative in the ledger because `--cherry-pick` and `range-diff` cannot recognize every conflict resolution.

If the integration must be backed out, use a merge-aware revert of M2 after explicit approval. Do not rewrite or delete M1; its ancestry records which upstream work was integrated.
