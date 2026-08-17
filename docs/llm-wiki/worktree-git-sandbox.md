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
| 2026-08-06 | Also grant the **common dir**; every backend exports `SYMPHONY_GIT_WRITABLE_ROOTS` | The SMA-25 grant covered locks only — blobs go to `<common>/objects`, which Symphony never granted. Belt-and-braces on codex 0.146.0 (see measurement below); the gap is real for anything that does not auto-grant. |
| 2026-08-06 | Move the Final History Gate from the agent to the orchestrator | The agent is the least privileged actor in the system; making it prove delivery meant a permission limit could park a finished ticket in `Blocked`. |
| 2026-08-06 | Host re-checks a `Blocked` ticket's history before opening an RCA | An RCA worker inherits the same sandbox and blocks identically, and an RCA ticket cannot open a further RCA — so the board dead-ended. |
| 2026-08-17 (FIX-TASK-10-1) | Resolve `dirty_overlap` merge collisions by restoring host uncommitted files | Host uncommitted modifications on files touched by the branch trigger the merge safety gate (`_RC_SKIP_DIRTY` / exit code 41); restoring uncommitted host changes restores clean merge preflight. |

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

## Measured, 2026-08-06 — the incident does not reproduce on current CLIs

Live runs against a real host repo + linked worktree **outside `/tmp`**:

| CLI | control write outside the workspace | new-blob `git add` without Symphony's grant |
|-----|--------------------------------------|--------------------------------------------|
| codex 0.146.0, `-s workspace-write` | **denied** (`Operation not permitted`) | **succeeded**, blob written to `<host>/.git/objects` |
| claude 2.1.223, `-p` | n/a — no OS sandbox active | **succeeded**, blob written to `<host>/.git/objects` |

So codex auto-grants a linked worktree's git dirs, and the reported
`Operation not permitted` could not be attributed to either backend on this
machine. The grant closes a real gap in Symphony's own logic; the thing that
actually removes the failure mode is the **host-side gate**, which does not
depend on what any CLI permits.

Two traps that invalidated earlier attempts — check both before trusting a
repro:

- codex `workspace-write` grants `[workdir, /tmp, $TMPDIR]` by default. Any
  experiment staged in `/tmp` is writable by construction and proves nothing.
- git deduplicates objects. Re-running `git add` on the same *content*
  succeeds without writing anything, because the blob already exists. Force a
  new object with unique content per run.

**Always run a control write** that must be denied. Without it, a success
reads identically whether the sandbox is permissive or the fix worked.

## Not covered

- Where the original `Operation not permitted` came from — an older codex, a
  different sandbox configuration, a container, or host file permissions. Not
  determined.
- Whether Claude Code's bash sandbox (not enabled here) denies `.git` writes
  when switched on; the `--add-dir` grant is defensive.
- Object *alternates* (`objects/info/alternates`) are ignored on purpose:
  they are read-only borrow sources, writes land in the primary database.
