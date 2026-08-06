"""Git write targets a sandboxed agent must be granted, and how to spot a denial.

A Symphony workspace is a *linked git worktree* of the host repo (the default
`after_create` hook, `scripts/symphony-setup-worktree.sh`). A worktree does not
carry its own repository: it splits git's writable state across two roots that
both live outside the workspace directory.

    <workspace>/.git                    pointer file, not a directory
    <host>/.git/worktrees/<ID>/index    per-worktree admin state (locks, HEAD)
    <host>/.git/objects/                SHARED object database

A directory-scoped sandbox (codex `workspace-write`, a `--add-dir` allow-list)
that grants only the workspace therefore lets `git add` take the index lock and
then fail while writing the blob:

    error: unable to create temporary file: Operation not permitted
    error: <path>: failed to insert into database
    fatal: updating files failed

Granting only the per-worktree admin dir moves the failure one step later but
does not remove it — the object database lives in the *common* dir one level
up. `writable_git_roots()` returns both so callers grant the whole set.

Everything here is filesystem reads of git's documented on-disk layout; no
subprocess, so it stays usable on a backend hot path and when `git` is absent
from PATH. Object *alternates* (`objects/info/alternates`) are deliberately
ignored: they are read-only borrow sources, and writes always land in the
primary object database resolved here.
"""

from __future__ import annotations

import os
from pathlib import Path

# `git worktree add` writes this file into the per-worktree admin dir. It holds
# the path back to the shared common dir that owns `objects/` and `refs/` —
# usually the relative `../..`, absolute when the repo was set up that way.
_COMMONDIR_FILE = "commondir"
_GITDIR_PREFIX = "gitdir:"
# Depth cap for the upward `.git` walk. A workspace is normally the repo root,
# but file-tracker setups nest it a few levels down; the cap keeps a workspace
# that is *not* in a repo from walking to the filesystem root on every call.
_MAX_PARENTS = 40


def resolve_git_dir(start: Path) -> Path | None:
    """Absolute git dir for ``start``, or None when it is not in a repo.

    Mirrors `git rev-parse --git-dir`: a regular repo answers ``<root>/.git``,
    a linked worktree answers ``<host>/.git/worktrees/<ID>`` by following the
    ``gitdir:`` pointer in its ``.git`` file.
    """
    try:
        current = start.resolve(strict=False)
    except OSError:
        return None
    for _ in range(_MAX_PARENTS):
        marker = current / ".git"
        try:
            if marker.is_dir():
                return marker
            if marker.is_file():
                return _read_gitdir_pointer(marker)
        except OSError:
            return None
        if current.parent == current:
            break
        current = current.parent
    return None


def _read_gitdir_pointer(marker: Path) -> Path | None:
    """Follow a worktree/submodule ``.git`` file to its admin directory.

    Reading the pointer is safe: it is plain text written by the same user that
    ran `git worktree add`, and a missing or garbled pointer degrades to None
    (treated by callers as "no extra root to grant").
    """
    try:
        content = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in content.splitlines():
        if not line.startswith(_GITDIR_PREFIX):
            continue
        target = line[len(_GITDIR_PREFIX) :].strip()
        if not target:
            return None
        path = Path(target)
        if not path.is_absolute():
            path = marker.parent / path
        try:
            return path.resolve(strict=False)
        except OSError:
            return None
    return None


def resolve_git_common_dir(start: Path) -> Path | None:
    """Absolute dir owning ``objects/`` and ``refs/``, or None outside a repo.

    Mirrors `git rev-parse --git-common-dir`. Equals the git dir for a regular
    repo; for a linked worktree it is the host repo's ``.git``, which is where
    every blob written by `git add` / `git commit` / `git merge-tree` lands.
    """
    git_dir = resolve_git_dir(start)
    if git_dir is None:
        return None
    common_file = git_dir / _COMMONDIR_FILE
    try:
        if not common_file.is_file():
            return git_dir
        target = common_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return git_dir
    if not target:
        return git_dir
    path = Path(target)
    if not path.is_absolute():
        path = git_dir / path
    try:
        return path.resolve(strict=False)
    except OSError:
        return git_dir


def is_linked_worktree(start: Path) -> bool:
    """Whether ``start`` sits in a linked worktree rather than a regular repo."""
    git_dir = resolve_git_dir(start)
    if git_dir is None:
        return False
    return git_dir != resolve_git_common_dir(start)


def writable_git_roots(*starts: Path) -> list[str]:
    """Every git directory a write inside ``starts`` can touch, deduped.

    Returns the git dir and the common dir for each input (identical for a
    regular repo, distinct for a linked worktree), skipping paths that do not
    exist. Sorted so backend command lines stay stable across runs — an
    unstable ordering would churn the injected sandbox flags and make the
    launch command harder to diff in logs.
    """
    roots: set[str] = set()
    for start in starts:
        for candidate in (resolve_git_dir(start), resolve_git_common_dir(start)):
            if candidate is None:
                continue
            try:
                if candidate.is_dir():
                    roots.add(str(candidate))
            except OSError:
                continue
    return sorted(roots)


# Every backend exports this so a wrapper script — or the agent CLI itself,
# for tools Symphony cannot inject flags into — can widen its own sandbox.
# `os.pathsep`-joined, absent when there is nothing extra to grant.
GIT_ROOTS_ENV_VAR = "SYMPHONY_GIT_WRITABLE_ROOTS"


def git_roots_outside(cwd: Path, *also_scan: Path) -> list[str]:
    """Git directories a sandbox scoped to ``cwd`` would not cover.

    ``cwd`` is the agent's working directory — the one directory every
    backend's sandbox grants — and is the only thing treated as already
    covered. ``also_scan`` adds further starting points to look from without
    implying they are writable; the workspace *root* in particular is a
    parent Symphony creates but does not hand to the agent, so a git dir
    living under it still has to be granted explicitly.

    A plain repo workspace answers empty — its ``.git`` is already inside
    ``cwd``. The default worktree workspace answers with the host repo's
    admin dir and common dir, which is exactly the grant whose absence made
    `git add` fail with ``failed to insert into database``.
    """
    try:
        covered = cwd.resolve(strict=False)
    except OSError:
        covered = cwd
    outside: set[str] = set()
    for start in (cwd, *also_scan):
        for root in writable_git_roots(start):
            if Path(root).is_relative_to(covered):
                continue
            outside.add(root)
    return sorted(outside)


def git_roots_env(cwd: Path, *also_scan: Path) -> dict[str, str]:
    """Env fragment carrying :func:`git_roots_outside`, empty when unneeded."""
    roots = git_roots_outside(cwd, *also_scan)
    return {GIT_ROOTS_ENV_VAR: os.pathsep.join(roots)} if roots else {}


# --- failure classification -------------------------------------------------

# Git's wording when the process reached the object database or admin dir but
# the OS refused the write. `failed to insert into database` and `unable to
# create temporary file` are emitted by the loose-object writer specifically,
# which is why they identify an object-database denial rather than any other
# git error.
_OBJECT_DB_DENIAL_MARKERS = (
    "failed to insert into database",
    "unable to create temporary file",
    "unable to create tmp-objdir",
    "cannot create temporary file",
)
# Generic OS refusals. On their own these are ambiguous — `permission denied`
# is also what an SSH push prints when the *key* is rejected — so a match here
# only counts alongside a local-write marker.
_OS_DENIAL_MARKERS = (
    "operation not permitted",
    "permission denied",
    "read-only file system",
)
# Local-write context that disambiguates a generic OS refusal.
_LOCAL_WRITE_MARKERS = (
    ".git/objects",
    ".git/worktrees",
    "index.lock",
    "head.lock",
    "updating files failed",
    "failed to write ref",
)
# Remote-side refusals. These are never a sandbox problem: retrying with wider
# filesystem permissions cannot fix an auth failure or a rejected push.
_REMOTE_FAILURE_MARKERS = (
    "could not read from remote repository",
    "authentication failed",
    "permission denied (publickey",
    "remote: ",
    "! [rejected]",
    "non-fast-forward",
    "failed to push some refs",
    "could not resolve host",
    "connection timed out",
)

SANDBOX_WRITE_DENIED = "sandbox_write_denied"
REMOTE_REJECTED = "remote_rejected"
UNKNOWN_FAILURE = "unknown"


def classify_history_failure(text: str) -> str:
    """Label a failed git history command by what would actually fix it.

    Returns one of :data:`SANDBOX_WRITE_DENIED` (the process could not write
    into the local object database or admin dir — wider writable roots or a
    host-side retry fixes it), :data:`REMOTE_REJECTED` (the local history is
    fine and the remote refused — only a human or a credential change fixes
    it), or :data:`UNKNOWN_FAILURE`.

    Remote failures are checked first: a push that fails authentication can
    still print `permission denied`, and mislabelling that as a sandbox
    problem would send the orchestrator into a retry that can never succeed.
    """
    blob = (text or "").lower()
    if not blob:
        return UNKNOWN_FAILURE
    if any(marker in blob for marker in _REMOTE_FAILURE_MARKERS):
        return REMOTE_REJECTED
    if any(marker in blob for marker in _OBJECT_DB_DENIAL_MARKERS):
        return SANDBOX_WRITE_DENIED
    if any(marker in blob for marker in _OS_DENIAL_MARKERS) and any(
        marker in blob for marker in _LOCAL_WRITE_MARKERS
    ):
        return SANDBOX_WRITE_DENIED
    return UNKNOWN_FAILURE
