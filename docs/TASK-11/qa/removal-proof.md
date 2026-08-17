# TASK-11 QA: profile-smoke.txt Removal Proof

Run: 2026-08-17T17:43Z, workspace `/home/symphony/symphony_workspaces/TASK-11`, branch `symphony/TASK-11`, tip `e9a5b22`.

## Goal

Prove the three acceptance criteria: the file is removed via a normal commit, it is no longer tracked, and develop history was not rewritten.

## Check transcript

### AC1 — file removed from repository (git rm, normal commit)

```
$ test ! -e profile-smoke.txt
(exit 0, no output)
```

```
$ git show --stat e9a5b22
commit e9a5b222855ee8282f24810e0be70f1bfc4280d7
Author: symphony <symphony@local>
Date:   Mon Aug 17 17:40:42 2026 +0000

    [no-test] wip: turn 2026-08-17T17:40:42Z

 docs/TASK-11/work/removal-notes.md | 23 +++++++++++++++++++++++
 profile-smoke.txt                  |  1 -
 2 files changed, 23 insertions(+), 1 deletion(-)
```

**Proves**: `profile-smoke.txt` is absent from the working tree, and the removal rides a normal commit (`profile-smoke.txt | 1 -`) on the feature branch — no `git revert`, no `git reset`.
**Does not prove**: the post-merge state on develop (covered by merge preflight below).

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

**Proves**: git index on the branch contains no path matching `profile-smoke` — equivalent to `git ls-files | grep profile-smoke` returning nothing (pathspec filter instead of pipe; workspace policy denies pipes).
**Does not prove**: post-merge develop state by itself; see `qa/merge-tree-evidence.md` — develop tip is the direct parent of the branch tip, so the merged tree is exactly the branch tree.

### AC3 — develop history not rewritten

```
$ git log -n 1 --oneline 62a5734
62a5734 merge: TASK-9 from symphony/TASK-9 (0fc8df9)
```

```
$ git merge-base --is-ancestor 62a5734 HEAD
(exit 0)
```

```
$ git diff 62a5734..HEAD --stat
 docs/TASK-11/work/removal-notes.md | 23 +++++++++++++++++++++++
 profile-smoke.txt                  |  1 -
 2 files changed, 23 insertions(+), 1 deletion(-)
```

**Proves**: the TASK-9 merge commit `62a5734` is still an ancestor of the branch tip, and the branch adds only one commit on top — history was extended, not rewritten.
**Does not prove**: that no force-push was ever issued remotely — no remote is configured in this workspace; the local history constraint is fully proven.

### Content check — original artifact

```
$ git show develop:profile-smoke.txt
OK
```

**Proves**: the deleted file on the target branch had exactly the single line `OK` — matches the ticket description of the TASK-9 artifact.

## How to re-run

In the workspace (single commands; pipes/compound commands are denied by policy):

1. `test ! -e profile-smoke.txt`
2. `git ls-files profile-smoke.txt`
3. `git merge-base --is-ancestor 62a5734 HEAD`
4. `git show --stat e9a5b22`

## Environment limitations (honest disclosure)

- `git merge-tree --write-tree develop symphony/TASK-11` — denied by the workspace permission policy ("This command requires approval"). See `qa/merge-tree.log` and `qa/merge-tree-evidence.md` for the fallback topology proof.
- `git -C /home/symphony/git/oh-my-symphony status --porcelain` (host dirty-tracked-file check) — denied by the same policy. Overlap risk assessed instead via `git diff --name-only develop..symphony/TASK-11`: only `profile-smoke.txt` and `docs/TASK-11/work/removal-notes.md`, neither of which is host-managed board state (`/kanban/` is gitignored).
