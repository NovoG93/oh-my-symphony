# worktree-git-sandbox

Why a sandboxed agent cannot commit inside a Symphony workspace, which git
directories have to be granted, and who owns the delivery record now.

## The split that breaks directory-scoped sandboxes

A Symphony workspace is a **linked git worktree** of the host repo
(`scripts/symphony-setup-worktree.sh`, the default `after_create` hook). A
worktree is not a copy of the repository. It splits git's writable state:

| what | where | measured with |
|------|-------|----------------|
| working tree | `<workspace>/` | — |
| `.git` | a *pointer file*, not a directory | `cat <workspace>/.git` |
| index, HEAD locks | `<host>/.git/worktrees/<ID>/` | `git rev-parse --git-dir` |
| **object database** | `<host>/.git/objects/` | `git rev-parse --git-common-dir` |

Both admin locations are outside the workspace directory. A sandbox that
grants only the workspace lets `git add` take the index lock and then fail
writing the blob:

```
error: unable to create temporary file: Operation not permitted
error: <path>: failed to insert into database
fatal: updating files failed
```

The absence of an `index.lock` error in that output is the tell: the admin
dir was writable, the object database was not.

## Decision log

| date | decision | why |
|------|----------|-----|
| 2026-05-17 (SMA-25) | Grant the per-worktree gitdir in codex `writable_roots` | Fixed `index.lock` denials |
| 2026-08-06 | Also grant the **common dir**; every backend exports `SYMPHONY_GIT_WRITABLE_ROOTS` | The SMA-25 fix only covered locks. Blobs go to `<common>/objects`, so `git add`, `git commit` and `git merge-tree --write-tree` still died. |
| 2026-08-06 | Move the Final History Gate from the agent to the orchestrator | The agent is the least privileged actor in the system; making it prove delivery meant a permission limit could park a finished ticket in `Blocked`. |
| 2026-08-06 | Host re-checks a `Blocked` ticket's history before opening an RCA | An RCA worker inherits the same sandbox and blocks identically, and an RCA ticket cannot open a further RCA — so the board dead-ended. |

## What to reuse

- `symphony.utils.git_sandbox` resolves both directories by reading git's
  on-disk layout (`.git` pointer, then `<gitdir>/commondir`) — no subprocess,
  safe on a backend hot path.
- `git_roots_outside(cwd, *also_scan)` treats **only `cwd`** as already
  granted. The workspace *root* is a parent Symphony creates but never hands
  to the agent, so a git dir under it still needs an explicit grant.
- `classify_history_failure()` checks remote failures first. SSH auth
  failures also print `permission denied`; labelling one as a sandbox problem
  would start a retry that can never succeed.
- `symphony doctor` probes the object database for real (`git history
  writable`) and reports whether the configured agent can be handed its roots
  (`agent git grant`).

## Not covered

- Whether Claude Code's own bash sandbox denies `.git` writes was never
  reproduced directly; the `--add-dir` grant is defensive. The host-side gate
  is what actually removes the failure mode, and it is backend-independent.
- Object *alternates* (`objects/info/alternates`) are ignored on purpose:
  they are read-only borrow sources, writes land in the primary database.
