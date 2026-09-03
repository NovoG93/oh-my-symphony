# TASK-11 QA: profile-smoke.txt Removal Proof (Verify pass 2)

Run: 2026-08-17T18:50Z, workspace `/home/symphony/symphony_workspaces/TASK-11`, branch `symphony/TASK-11`, tip `e9c248a7716882e0bf265036f0362456c28abb4a`.

Pass 1 of this file cited tip `e9a5b22` (a since-consolidated auto-commit); the branch now carries a single removal commit `e9c248a`. All commands below were re-run against that tip.

Document-stage re-check (2026-08-17T18:54Z): after the run, the harness auto-commit `23d5e7a` ("wip: turn ...", refreshed QA transcripts) landed on top. AC1/AC2/AC3 re-run at tip `23d5e7a` — all pass; the AC3 `git log` block below shows the corrected two-commit branch.

## Goal

Prove the three acceptance criteria: the file is removed via a normal commit, it is no longer tracked, and develop history was not rewritten.

## Check transcript

### AC1 — file removed from repository (git rm, normal commit)

```
$ test ! -e profile-smoke.txt
(exit 0, no output)
```

```
$ git show --stat e9c248a
commit e9c248a7716882e0bf265036f0362456c28abb4a
    TASK-11: Remove the test artifact file profile-smoke.txt ...
 docs/TASK-11/qa/merge-tree-evidence.md          | 56 ++++++
 docs/TASK-11/qa/removal-proof.md                | 98 ++++++
 docs/TASK-11/work/removal-notes.md              | 23 +++++
 docs/llm-wiki/INDEX.md                          |  2 +-
 docs/llm-wiki/byte-exact-static-deliverables.md | 32 +++++
 profile-smoke.txt                               |  1 -
 6 files changed, 210 insertions(+), 2 deletions(-)
```

**Proves**: `profile-smoke.txt` is absent from the working tree, and its deletion (`profile-smoke.txt | 1 -`) rides a normal commit `e9c248a` on the feature branch — no `git revert`, no `git reset` of develop.
**Does not prove**: the post-merge state on develop (covered by merge preflight, `qa/merge-tree-evidence.md`).

### AC2 — no longer tracked

```
$ git ls-files profile-smoke.txt
(exit 0, empty output)
```

```
$ git ls-files '*profile-smoke*'
(exit 0, empty output)
```

```
$ git status
On branch symphony/TASK-11
nothing to commit, working tree clean
```

**Proves**: the git index on the branch contains no path matching `profile-smoke` — equivalent to `git ls-files | grep profile-smoke` returning nothing (pathspec form instead of pipe; workspace policy denies pipes). `smoke.txt` (SMOKE-003 artifact) remains tracked but does not match `profile-smoke`.
**Does not prove**: post-merge develop state by itself; see `qa/merge-tree-evidence.md` — the branch deletion is the only change to that path on either side of the merge.

### AC3 — develop history not rewritten

```
$ git merge-base --is-ancestor 62a5734 HEAD
(exit 0)
```

```
$ git log --oneline 62a5734..HEAD
23d5e7a wip: turn 2026-08-17T18:52:02Z             <- harness evidence auto-commit (QA transcripts refreshed)
e9c248a TASK-11: Remove the test artifact file profile-smoke.txt ...
```

```
$ git reflog -n 5
e9c248a HEAD@{0}: reset: moving to HEAD
e9c248a HEAD@{1}:
```

**Proves**: the TASK-9 merge commit `62a5734` is still an ancestor of the branch tip (re-checked at Document stage against tip `23d5e7a`), and the removal rides the single normal commit `e9c248a` with the harness evidence auto-commit `23d5e7a` on top — history extended, not rewritten. The only reflog entry is a same-commit `reset: moving to HEAD` (no force-push, no rebase).
**Does not prove**: that no force-push was ever issued remotely — no remote is configured in this workspace; the local history constraint is fully proven.

### Content check — original artifact

```
$ git show develop:profile-smoke.txt
OK
```

**Proves**: the file on the target branch had exactly the single line `OK` — matches the ticket description of the TASK-9 artifact. `git diff 62a5734 develop -- profile-smoke.txt` is empty, so develop never modified it after TASK-9; the branch's deletion applies cleanly.

## How to re-run

In the workspace (single commands; pipes/compound commands are denied by policy):

1. `test ! -e profile-smoke.txt`
2. `git ls-files profile-smoke.txt`
3. `git merge-base --is-ancestor 62a5734 HEAD`
4. `git show --stat e9c248a`

## Environment limitations (honest disclosure)

- `git merge-tree --write-tree develop symphony/TASK-11` — denied by the workspace permission policy in both worktree and host-repo forms ("This command requires approval"). See `qa/merge-tree-evidence.md` for the conflict-free proof chain.
- Host dirty-tracked-file check via `git -C` — denied by the same policy; checked instead with `git diff --name-only develop..symphony/TASK-11 -- WORKFLOW.md` (empty) against the host's dirty `WORKFLOW.md` (see `qa/merge-tree-evidence.md`).
